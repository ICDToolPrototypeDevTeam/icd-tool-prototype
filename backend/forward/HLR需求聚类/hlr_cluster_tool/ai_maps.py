# -*- coding: utf-8 -*-
"""
AI 辅助映射表的生成与增量更新
==============================
三个 AI 辅助映射表（与 cluster_hlr.py 配套）：

  name_map   : req_id -> 信号名                     （全量覆盖：每条需求都应有条目，null 表示确认无信号名）
  state_map  : req_id -> {OneState, ZeroState}      （部分覆盖：只含涉及 0/1 状态定义的需求）
  signal_map : req_id -> [接口项{label,name,bit,direction}]（部分覆盖：只含涉及 ≥2 个信号的需求）

增量更新策略
------------
  需求文档变化后，脚本自动比对需求集合与映射键，分三类处理：

    stale        映射里有、需求文档里没有   -> 直接删除（需求已删）
    missing      需求文档里有、映射里没有   -> 交给 AI 生成
    text_changed 需求 ID 未变但原文已改     -> 交给 AI 重新生成（原值可能过时）

  未受影响的条目原样保留（含 hash），因此**只对变化的部分调用 AI**，成本可控。
  更新前自动备份为 `<文件>.bak`。

单独使用
--------
  python ai_maps.py --docx 需求.docx --map name   --out name_map.json
  python ai_maps.py --docx 需求.docx --map state  --out state_map.json --force
  python ai_maps.py --docx 需求.docx --map signal --out signal_map.json --dry-run

  --force     忽略增量，全部重新生成
  --dry-run   只打印将要处理的需求，不实际调用 AI
  --ai-config / --ai-base / --ai-key / --ai-model    AI 接口配置（见 ai_client.py）
"""
import os
import re
import sys
import json
import math
import shutil
import hashlib
import argparse

from ai_client import AIClient, AIError

# ----------------------------------------------------------------------------
# 提示词
# ----------------------------------------------------------------------------
NAME_SYSTEM = '你是民机机载软件接口需求分析专家，负责从软件高层需求（HLR）文本中提取接口信号名。'

NAME_USER = """\
下面是软件高层需求清单，请逐条提取该需求所操作的接口"信号名"。

规则：
1. **只依据给定文本提取**，禁止引入外部知识、禁止臆造。
2. 优先采用文本中显式标注的名字，如"（信号名：风扇速度SPEED）"→ "风扇速度SPEED"。
3. 其次从"解析X""读取X""采集X""写入X""为X""将X…写入""X设为…"等句式中取 X（信号/参数名）。
4. 名字要精简：去掉动词、介词与修饰前缀（如"软件应""应""解析""写入""将""离散量""模拟量""一次""周期发送"等）；
   去掉 label 前缀（如"L11压调状态字"→"压调状态字"）；去掉"的"（"RFAN的工作模式"→"RFAN工作模式"）。
5. 名字一般 2~20 字，可含英文与数字，如：AFTEFAN1速度信号、UTC时间、FWD_CED2供电电压回采值、飞机注册号。
6. 若文本确实没有给出信号名（例如只写了 LABEL 号、只描述整字发送、只给出 bit 位定义），值为 null。
7. 若需求操作的是 A429 固定格式字段（SDI / SSM / Parity），信号名取该字段名（如 "SDI"）。

需求清单：
{reqs}

只输出 JSON 对象，键为需求 ID，值为信号名字符串或 null。不要输出任何解释文字。
"""

STATE_SYSTEM = '你是民机机载软件接口需求分析专家，负责从需求文本中提取离散状态的 0/1 取值定义。'

STATE_USER = """\
下面是软件高层需求清单，请提取每条需求里 bit 的 0/1 取值状态定义。

规则：
1. **只依据给定文本提取**，禁止臆造。
2. OneState = bitX=1 时对应的状态含义；ZeroState = bitX=0 时对应的状态含义。
3. 一条需求若有多个 bit 各自定义状态，用分号连接，每段格式：bitN=1→含义。
4. 注意倒装语序："当XX标志为'有效'时 bit22=1" → OneState 记作 "bit22=1→XX标志有效"。
5. 组合编码（如 SDI 通道位置编码：1A 时 bit8=0,bit9=0；1B 时 bit8=1,bit9=0）同属状态信息，
   按 bit 组合归纳为状态描述。
6. 状态含义取自文本原词（有效/无效/True/False/失效/未失效/FAULT 等），不要改写、不要翻译。
7. 只输出**确实包含 bit 取值状态定义**的需求；纯数值型、整字发送型、位域长度型的需求不要出现在结果里。

需求清单：
{reqs}

只输出 JSON 对象，格式：
{{"需求ID": {{"OneState": "bit22=1→...", "ZeroState": "bit22=0→..."}}}}
不要输出任何解释文字。
"""

SIGNAL_SYSTEM = '你是民机机载软件接口需求分析专家，负责把"一条需求涉及多个信号"的需求按信号拆分为独立接口项。'

SIGNAL_USER = """\
下面是软件高层需求清单，请把其中**涉及多个信号**的需求拆分为独立接口项。

规则：
1. **只依据给定文本拆分**，禁止臆造信号名、bit 或 label。
2. 每个接口项含四个字段：
   - label：不带 L 前缀的数字（如 "11"），文本未给出则 null；
   - name：该信号的名称，精简（去动词/介词/修饰前缀，去"的"）；
   - bit：该信号对应的 bit 号或范围（如 "22"、"13-28"），未给出则 null；
   - direction："TX"=发送/写入，"RX"=接收/解析。
3. 只输出**真正涉及 ≥2 个独立信号**的需求；单信号需求不要出现在结果里。
4. 同一 bit 范围内的多个 bit 各自对应不同信号时（如 bit22~bit28 分别是不同标志），逐个拆开。
5. 不要拆分"一个信号占用多个 bit"的情况（那仍是单信号）。

需求清单：
{reqs}

只输出 JSON 对象，格式：
{{"需求ID": [{{"label":"11","name":"压调OFVTRV失效在关位故障有效标志","bit":"22","direction":"TX"}}]}}
不要输出任何解释文字。
"""


# ----------------------------------------------------------------------------
# 工具
# ----------------------------------------------------------------------------
def text_hash(text):
    """需求中文指纹（md5 前 8 位）—— 与 cluster_hlr.py 保持一致"""
    return hashlib.md5((text or '').encode('utf-8')).hexdigest()[:8]


def _chunk(seq, n):
    n = max(1, int(n))
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def _format_reqs(reqs):
    return '\n'.join(f"需求ID: {r['id']}\n需求中文: {r['需求中文']}\n" for r in reqs).strip()


def _norm_existing(raw):
    """归一化已有映射为 {req_id: {'value':..., 'text_hash':...}}（跳过 __meta__ 等元数据键）"""
    out = {}
    for k, v in (raw or {}).items():
        if str(k).startswith('__'):
            continue
        if isinstance(v, dict) and ('value' in v or 'text_hash' in v):
            out[k] = {'value': v.get('value'), 'text_hash': v.get('text_hash')}
        else:
            out[k] = {'value': v, 'text_hash': None}
    return out


# 映射文件内的元数据键：记录"上次生成时需求文档里有哪些需求"，
# 用于把 partial 覆盖映射的"新增需求"与"本来就不需要该属性的需求"区分开，
# 避免每次运行都把同一批需求重复送 AI（省 token）。
META_KEY = '__meta__'


def _req_sort_key(rid):
    m = re.search(r'(\d+)$', str(rid))
    return (re.sub(r'\d+$', '', str(rid)), int(m.group(1)) if m else 0, str(rid))


# ----------------------------------------------------------------------------
# 各映射表的 AI 生成器
# ----------------------------------------------------------------------------
def build_name_values(client, reqs, log=print):
    """返回 {req_id: 信号名 or None}（全量：未返回的按 None）"""
    values = {}
    batch_size = int(client.cfg.get('batch_size', 20))
    batches = list(_chunk(reqs, batch_size))
    for bi, batch in enumerate(batches, 1):
        log(f'    name_map 第 {bi}/{len(batches)} 批（{len(batch)} 条）…')
        data = client.chat_json(NAME_SYSTEM, NAME_USER.format(reqs=_format_reqs(batch)))
        if not isinstance(data, dict):
            log(f'    [警告] 第 {bi} 批返回结构异常，已跳过')
            continue
        for r in batch:
            v = data.get(r['id'])
            values[r['id']] = (str(v).strip() or None) if v is not None and str(v).strip() else None
    return values


def build_state_values(client, reqs, log=print):
    """返回 {req_id: {'OneState':..,'ZeroState':..}}（仅含有状态定义的需求）"""
    values = {}
    batch_size = int(client.cfg.get('batch_size', 20))
    batches = list(_chunk(reqs, batch_size))
    for bi, batch in enumerate(batches, 1):
        log(f'    state_map 第 {bi}/{len(batches)} 批（{len(batch)} 条）…')
        data = client.chat_json(STATE_SYSTEM, STATE_USER.format(reqs=_format_reqs(batch)))
        if not isinstance(data, dict):
            log(f'    [警告] 第 {bi} 批返回结构异常，已跳过')
            continue
        for r in batch:
            v = data.get(r['id'])
            if not isinstance(v, dict):
                continue
            one = str(v.get('OneState')).strip() if v.get('OneState') else None
            zero = str(v.get('ZeroState')).strip() if v.get('ZeroState') else None
            if one or zero:
                values[r['id']] = {'OneState': one or None, 'ZeroState': zero or None}
    return values


def build_signal_values(client, reqs, log=print):
    """返回 {req_id: [接口项,…]}（仅含 ≥2 个信号的需求）"""
    values = {}
    batch_size = int(client.cfg.get('batch_size', 20))
    batches = list(_chunk(reqs, batch_size))
    for bi, batch in enumerate(batches, 1):
        log(f'    signal_map 第 {bi}/{len(batches)} 批（{len(batch)} 条）…')
        data = client.chat_json(SIGNAL_SYSTEM, SIGNAL_USER.format(reqs=_format_reqs(batch)))
        if not isinstance(data, dict):
            log(f'    [警告] 第 {bi} 批返回结构异常，已跳过')
            continue
        for r in batch:
            items = data.get(r['id'])
            if not isinstance(items, list):
                continue
            norm = []
            for it in items:
                if not isinstance(it, dict):
                    continue
                lab = it.get('label')
                if lab not in (None, ''):
                    lab = re.sub(r'^(?:LABEL|L)', '', str(lab).strip()) or None
                else:
                    lab = None
                nm = it.get('name')
                nm = str(nm).strip() or None if nm not in (None, '') else None
                bt = it.get('bit')
                bt = str(bt).strip() or None if bt not in (None, '') else None
                dr = it.get('direction')
                dr = dr if dr in ('TX', 'RX') else None
                norm.append({'label': lab, 'name': nm, 'bit': bt, 'direction': dr})
            if len(norm) >= 2:
                values[r['id']] = norm
    return values


MAP_SPECS = {
    'name': {'coverage': 'full', 'builder': build_name_values, 'file': 'name_map.json'},
    'state': {'coverage': 'partial', 'builder': build_state_values, 'file': 'state_map.json'},
    'signal': {'coverage': 'partial', 'builder': build_signal_values, 'file': 'signal_map.json'},
}


# ----------------------------------------------------------------------------
# 增量更新
# ----------------------------------------------------------------------------
def update_map(kind, client, reqs, path, force=False, scan_new=True, dry_run=False,
               source_doc=None, log=print):
    """按一致性状态增量更新一个映射文件。返回统计信息 dict。

    kind       : 'name' | 'state' | 'signal'
    reqs       : [{'id','需求中文',...}]
    force      : 忽略增量判断，全部重新生成
    scan_new   : partial 覆盖时，是否对**新增需求**也做 AI 扫描（默认 True，
                 避免新需求的状态定义/多信号被漏掉）
    dry_run    : 只打印计划，不调用 AI、不写文件
    source_doc : 需求文档名（写入 __meta__，便于溯源）
    """
    spec = MAP_SPECS[kind]
    coverage = spec['coverage']

    existing = {}
    prev_covered = set()
    if os.path.exists(path):
        with open(path, encoding='utf-8') as f:
            raw = json.load(f)
        prev_covered = set((raw.get(META_KEY) or {}).get('covered_reqs') or [])
        existing = _norm_existing(raw)
    if not prev_covered:                      # 旧格式（无 __meta__）：退回键集合
        prev_covered = set(existing)

    reqs_by_id = {r['id']: r for r in reqs}
    hashes = {rid: text_hash(r.get('需求中文', '')) for rid, r in reqs_by_id.items()}

    stale = sorted(set(existing) - set(reqs_by_id), key=_req_sort_key)
    missing = sorted(set(reqs_by_id) - set(existing), key=_req_sort_key)
    # 真正新增的需求（上次生成时文档里还没有）—— partial 覆盖下用它而不是 missing
    new_reqs = sorted(set(reqs_by_id) - prev_covered, key=_req_sort_key)
    changed = sorted((rid for rid in set(existing) & set(reqs_by_id)
                      if existing[rid].get('text_hash') and existing[rid]['text_hash'] != hashes[rid]),
                     key=_req_sort_key)

    if force:
        to_gen = sorted(reqs_by_id, key=_req_sort_key)
    elif coverage == 'full':
        to_gen = sorted(set(changed) | set(missing), key=_req_sort_key)
    elif scan_new:
        to_gen = sorted(set(changed) | set(new_reqs), key=_req_sort_key)
    else:
        to_gen = sorted(set(changed), key=_req_sort_key)

    stats = {'kind': kind, 'path': path, 'coverage': coverage,
             'stale': stale, 'missing': missing, 'new_reqs': new_reqs,
             'text_changed': changed, 'regenerated': to_gen,
             'kept': 0, 'written': 0, 'dry_run': bool(dry_run)}

    if not to_gen and not stale:
        log(f'  [{kind}] 无变化，跳过')
        stats['skipped'] = True
        return stats

    if to_gen:
        bs = int(client.cfg.get('batch_size', 20))
        log(f'  [{kind}] 待 AI 处理 {len(to_gen)} 条（约 {math.ceil(len(to_gen)/bs)} 批）：'
            f'stale={len(stale)} 新增={len(new_reqs)} 原文已变={len(changed)}')
        if dry_run:
            log(f'    [dry-run] 将处理：{to_gen}')
            return stats

    if not client.enabled:
        raise AIError('未配置 api_key，无法调用 AI 更新映射。'
                      '请设置环境变量 HLR_AI_API_KEY、或填写 ai_config.json。')

    values = spec['builder'](client, [reqs_by_id[x] for x in to_gen], log=log) if to_gen else {}

    # ---- 合并 ----
    new_raw = {}
    for rid, item in existing.items():
        if rid in stale or rid in to_gen:
            continue
        # 保留 hash 未知的旧条目（兼容无 hash 的简单格式），否则写入当前 hash
        new_raw[rid] = {'value': item['value'],
                        'text_hash': item.get('text_hash') or hashes.get(rid)}
        stats['kept'] += 1
    for rid in to_gen:
        v = values.get(rid)
        if coverage == 'full':
            new_raw[rid] = {'value': v, 'text_hash': hashes[rid]}
            stats['written'] += 1
        elif v:
            # 部分覆盖：仅当 AI 判定确有内容时才写入；未返回即表示该需求不再涉及
            new_raw[rid] = {'value': v, 'text_hash': hashes[rid]}
            stats['written'] += 1

    if dry_run:
        return stats

    # ---- 备份 + 写回 ----
    if os.path.exists(path):
        backup = path + '.bak'
        try:
            shutil.copy2(path, backup)
            stats['backup'] = backup
        except Exception as e:
            log(f'    [警告] 备份失败：{e}')
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)

    payload = {META_KEY: {
        'coverage': coverage,
        'source_doc': source_doc,
        'generated_at': __import__('datetime').datetime.now().isoformat(timespec='seconds'),
        # 本次处理时需求文档里的全部需求 ID —— 下次据此精确识别"新增需求"
        'covered_reqs': sorted(reqs_by_id, key=_req_sort_key),
    }}
    payload.update({k: new_raw[k] for k in sorted(new_raw, key=_req_sort_key)})
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    log(f'    [{kind}] 已写回：{path}（新增/更新 {stats["written"]} 条，'
        f'保留 {stats["kept"]} 条，删除 {len(stale)} 条）')
    return stats


def update_all_maps(client, reqs, paths, force=False, scan_new=True, dry_run=False,
                    source_doc=None, log=print):
    """依次更新三个映射表。paths: {'name': 路径, 'state': 路径, 'signal': 路径}"""
    results = {}
    for kind in ('name', 'state', 'signal'):
        path = paths.get(kind)
        if not path:
            continue
        results[kind] = update_map(kind, client, reqs, path,
                                   force=force, scan_new=scan_new, dry_run=dry_run,
                                   source_doc=source_doc, log=log)
    return results


# ----------------------------------------------------------------------------
# 单独运行
# ----------------------------------------------------------------------------
def main():
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser(description='AI 辅助映射表的生成与增量更新')
    ap.add_argument('--docx', default=os.path.join(here, 'example', 'HLR_完整未故障注入.docx'),
                    help='需求文档 .docx 路径')
    ap.add_argument('--map', default='name', choices=['name', 'state', 'signal'],
                    help='要更新的映射表：name / state / signal')
    ap.add_argument('--out', default=None, help='映射文件路径（默认取脚本同目录的 <map>_map.json）')
    ap.add_argument('--force', action='store_true', help='忽略增量判断，全部重新生成')
    ap.add_argument('--no-scan-new', action='store_true',
                    help='部分覆盖的映射（state/signal）不对新增需求做扫描')
    ap.add_argument('--dry-run', action='store_true', help='只打印计划，不调用 AI、不写文件')
    ap.add_argument('--ai-config', default=None, help='AI 配置文件 ai_config.json')
    ap.add_argument('--ai-base', default=None, help='覆盖 base_url')
    ap.add_argument('--ai-key', default=None, help='覆盖 api_key')
    ap.add_argument('--ai-model', default=None, help='覆盖 model')
    args = ap.parse_args()

    out = args.out or os.path.join(here, MAP_SPECS[args.map]['file'])
    client = AIClient.from_config(args.ai_config,
                                  {'base_url': args.ai_base, 'api_key': args.ai_key, 'model': args.ai_model},
                                  explicit=bool(args.ai_config))
    print(f'AI 配置: {client.describe()}')

    from cluster_hlr import parse_docx          # 延迟导入，避免循环依赖
    reqs, _ = parse_docx(args.docx)
    print(f'需求文档: {args.docx}（{len(reqs)} 条）')

    try:
        stats = update_map(args.map, client, reqs, out,
                           force=args.force, scan_new=not args.no_scan_new,
                           dry_run=args.dry_run,
                           source_doc=os.path.basename(args.docx))
    except AIError as e:
        print(f'AI 更新失败：{e}', file=sys.stderr)
        sys.exit(1)

    print()
    print(f'结果：stale={len(stats["stale"])} missing={len(stats["missing"])} '
          f'text_changed={len(stats["text_changed"])} 重新生成={len(stats["regenerated"])} '
          f'写入={stats["written"]} 保留={stats["kept"]}')
    print(f'AI 调用 {client.call_count} 次，累计 tokens={client.usage_tokens}')
    if stats.get('backup'):
        print(f'原文件已备份：{stats["backup"]}')


if __name__ == '__main__':
    main()
