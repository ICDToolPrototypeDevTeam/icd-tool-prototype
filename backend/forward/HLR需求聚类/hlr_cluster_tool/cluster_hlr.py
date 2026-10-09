# -*- coding: utf-8 -*-
"""
HLR 软件高层需求聚类工具
==========================
把软件高层需求文档（.docx）中的需求，按"需求文本中明确提及的属性"聚类，
输出 JSON。属性清单来自 eoicd_crop.yaml（publisher/subscriber 视角下各总线/层
定义的接口属性，去重后作为"类"的框架）。

核心原则：
  1. 只处理需求文档本身，不读取、不匹配 ICD / EoICD 接口文件；
  2. 需求文本中提到某属性，才归入该属性类；未提及的属性类为空；
  3. 一条需求可出现在多个类；类按属性名划分（不按属性值）；
  4. 每条归类输出"文本证据"，便于人工核对；
  5. 属性检测范围 = 整条需求表格的所有相关字段（需求中文、基本原理等），
     不只限于"需求中文"字段；
  6. 单个需求涉及多个信号时，按信号 Name 拆分接口项（AI 辅助 signal_map），
     每个接口项有独立的 label/name/bit/direction 与属性类；
  7. 涉及 "0"/"1" 编码定义的需求归入 OneState、ZeroState、CodedSet 三个类。

用法：
  python cluster_hlr.py --docx 需求.docx --yaml eoicd_crop.yaml --out 结果.json
  （省略参数时使用脚本内默认路径）

依赖：Python 3.8+，PyYAML（pip install pyyaml），其余为标准库。
"""
import os
import re
import sys
import json
import hashlib
import zipfile
import argparse
from xml.etree import ElementTree as ET
from collections import OrderedDict

try:
    import yaml
except ImportError:
    print("缺少依赖 PyYAML，请先执行：pip install pyyaml", file=sys.stderr)
    sys.exit(1)

W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
NS = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}

# 属性检测/身份提取时不参与检测的元数据字段（兼容各系统字段名差异，见 is_meta_field）
DETECT_EXCLUDE = {'需求ID', '对象类型', '是否衍生', '安全相关'}
# 属性检测字段优先级（影响 evidence 顺序）
DETECT_FIELD_ORDER = ['需求中文', '基本原理', '验证方法', '实现方法']


def is_meta_field(name):
    """元数据字段判定：这类字段只描述需求的管理属性（编号/类型/是否衍生/安全等级/来源），
    不含接口属性信息，不参与任何检测。

    用**命名规则**而非硬编码清单，兼容各系统字段名差异——如 AMS 叫"安全相关"，
    FGMC 叫"是否安全性相关"，早年硬编码清单漏掉了后者，导致它被当成普通字段扫描。
    """
    n = str(name).strip()
    if n in DETECT_EXCLUDE:
        return True
    return ('ID' in n.upper() or n.startswith('是否') or '安全' in n
            or '衍生' in n or '对象类型' in n or '来源' in n)


# ---------------- 0. 命令行参数 ----------------
def parse_args():
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser(description='HLR 软件高层需求聚类工具')
    ap.add_argument('--docx', default=os.path.join(here, 'example', 'HLR_完整未故障注入.docx'),
                    help='需求文档 .docx 路径')
    ap.add_argument('--yaml', default=os.path.join(here, 'eoicd_crop.yaml'),
                    help='属性清单 .yaml 路径')
    ap.add_argument('--out', default=os.path.join(here, 'example', 'HLR聚类结果.json'),
                    help='输出 JSON 路径')
    ap.add_argument('--dedup-out', default=None,
                    help='需求级去重结果 JSON 路径（默认与 --out 同目录，文件名将"聚类结果"替换为"需求去重结果"）')
    ap.add_argument('--name-map', default=os.path.join(here, 'name_map.json'),
                    help='AI 辅助识别的信号名映射 JSON（req_id -> 信号名），提供时 Name 属性值优先用它覆盖正则提取；默认同目录 name_map.json')
    ap.add_argument('--state-map', default=os.path.join(here, 'state_map.json'),
                    help='AI 辅助识别的离散状态映射 JSON（req_id -> {OneState, ZeroState}），值 null 表示该需求不涉及状态定义；提供时覆盖正则提取；默认同目录 state_map.json')
    ap.add_argument('--signal-map', default=os.path.join(here, 'signal_map.json'),
                    help='AI 辅助识别的信号级拆分映射 JSON（req_id -> 接口项列表 [{label,name,bit,direction},...]），'
                         '多信号需求按信号拆分为独立接口项；提供时优先于 label 级拆分；默认同目录 signal_map.json')
    ap.add_argument('--debug', action='store_true', help='在终端打印每个属性类的成员')
    ap.add_argument('--no-auto-split', action='store_true',
                    help='关闭"多信号需求自动拆分"（默认：需求列举 ≥2 个英文信号名时按信号拆为独立接口项）')
    # ---- AI 接口（可选，用于需求变更后自动更新映射表）----
    ap.add_argument('--ai-update', action='store_true',
                    help='检测到需求变更（新增/删除/原文改动）时，调用 AI 增量更新 name/state/signal 映射表')
    ap.add_argument('--ai-force', action='store_true',
                    help='配合 --ai-update：忽略增量判断，全部重新生成映射')
    ap.add_argument('--ai-dry-run', action='store_true',
                    help='配合 --ai-update：只打印将要处理的需求，不实际调用 AI、不写文件')
    ap.add_argument('--no-ai-scan-new', action='store_true',
                    help='state/signal 映射为部分覆盖，默认也对新增需求做 AI 扫描；加此参数则不扫描新增需求')
    ap.add_argument('--ai-config', default=None,
                    help='AI 接口配置文件（默认同目录 ai_config.json，可被环境变量/命令行覆盖）')
    ap.add_argument('--ai-base', default=None, help='覆盖 AI base_url（OpenAI 兼容）')
    ap.add_argument('--ai-key', default=None, help='覆盖 AI api_key')
    ap.add_argument('--ai-model', default=None, help='覆盖 AI model 名称')
    return ap.parse_args()


# ---------------- 1. AI 辅助映射加载与一致性校验 ----------------
def load_ai_map(path, label):
    """加载 AI 辅助映射，支持两种格式：
    简单格式:   {"req_id": "值"} 或 {"req_id": {"OneState":..., "ZeroState":...}}
    带hash格式: {"req_id": {"value": <值>, "text_hash": "需求原文md5前8位"}}
    返回 {req_id: {"value":..., "text_hash":...|None}}
    注：以 __ 开头的键为元数据（如 __meta__），不参与映射。
    """
    with open(path, encoding='utf-8') as f:
        data = json.load(f)
    out = {}
    for k, v in data.items():
        if str(k).startswith('__'):
            continue
        if isinstance(v, dict) and ('value' in v or 'text_hash' in v):
            out[k] = {'value': v.get('value'), 'text_hash': v.get('text_hash')}
        else:
            out[k] = {'value': v, 'text_hash': None}
    return out


def check_ai_map(map_data, reqs, label, coverage='full'):
    """ID 级 + 内容级一致性校验：需求更新后自动提示映射需重新生成
    missing=新增需求（无映射，走正则兜底，仅 full 覆盖提示）；
    stale=已删需求的冗余映射；text_changed=需求中文已变但 ID 未变（映射值可能过时）
    coverage: 'full'=映射应覆盖全部需求（如 name_map）；'partial'=只覆盖部分需求（如 state_map，缺失属正常）
    """
    req_ids = {r['id'] for r in reqs}
    map_ids = set(map_data.keys())
    missing = sorted(req_ids - map_ids)
    stale = sorted(map_ids - req_ids)
    text_changed = []
    hash_of = {r['id']: hashlib.md5(r['需求中文'].encode('utf-8')).hexdigest()[:8] for r in reqs}
    for rid in sorted(map_ids & req_ids):
        mh = map_data[rid]['text_hash']
        if mh and mh != hash_of.get(rid):
            text_changed.append(rid)
    status = {'mapped': len(req_ids & map_ids), 'missing': missing, 'stale': stale,
              'text_changed': text_changed}
    if (missing and coverage == 'full') or stale or text_changed:
        print(f"提示：需求与 {label} 不一致 —— "
              f"{'新增 ' + str(len(missing)) + ' 条（无映射，走正则兜底）、' if missing and coverage == 'full' else ''}"
              f"失效 {len(stale)} 条、原文已变 {len(text_changed)} 条（映射值可能过时）",
              file=sys.stderr)
        if missing and coverage == 'full':
            print(f"  新增需求: {missing}", file=sys.stderr)
        if stale:
            print(f"  失效映射: {stale}", file=sys.stderr)
        if text_changed:
            print(f"  原文已变更（需重新生成映射）: {text_changed}", file=sys.stderr)
    return status


# ---------------- 2. 属性清单（eoicd_crop.yaml） ----------------
def load_attrs(yaml_path):
    with open(yaml_path, encoding='utf-8') as f:
        crop = yaml.safe_load(f)
    order, sources = [], {}
    for view in crop:
        for bus, cfg in crop[view].items():
            for layer, alist in cfg['layers'].items():
                for a in alist:
                    sources.setdefault(a, []).append(f"{view}.{bus}.{layer}")
                    if a not in order:
                        order.append(a)
    return order, sources


# ---------------- 3. 解析 docx 需求 ----------------
# 需求表格字段名别名：各系统表头写法不一致，统一到标准字段名后再处理
FIELD_ALIASES = {
    '需求ID': ['需求ID', '需求Id', '需求编号', '需求标识', '需求条目编号'],
    '需求中文': ['需求中文', '需求描述', '需求正文', '需求内容', '需求文本', '需求名称', '需求说明'],
    '基本原理': ['基本原理', '原理', '设计原理', '实现原理', '需求原理'],
    '验证方法': ['验证方法', '验证方式', '验证手段'],
    '实现方法': ['实现方法', '实现方式', '实现手段'],
    '对象类型': ['对象类型', '需求类型'],
    '是否衍生': ['是否衍生', '衍生'],
    '安全相关': ['安全相关', '安全性', '安全等级'],
    '是否为需求': ['是否为需求', '是否需求', '是否作为需求'],
}
_ALIAS_LOOKUP = {a: std for std, aliases in FIELD_ALIASES.items() for a in aliases}

# 各系统用这些占位符表示"无内容"，统一按空处理（否则会被当成有效值参与检测/一致性校验）
_NULL_VALUES = {'', 'n/a', 'na', 'none', 'null', '无', '／', '/', '-', '—', '－', '待定', 'tbd'}
# "是否为需求"字段表示"不是需求"的取值
_NOT_REQ_VALUES = {'否', 'n', 'no', 'false', '0'}


def normalize_fields(raw_kv):
    """把各系统的表头写法统一为标准字段名，并把 N/A 之类占位符归一为空串"""
    out = {}
    for k, v in raw_kv.items():
        key = _ALIAS_LOOKUP.get(k.strip(), k.strip())
        val = (v or '').strip()
        if val.lower() in _NULL_VALUES:
            val = ''
        out[key] = val
    return out


def parse_docx(path):
    """**只从表格解析需求**。

    表格之外的正文段落（"范围/术语/缩略语/设备概述/软件概述"等，以及各需求表格前的小标题）
    一律**不参与任何提取**——它们只是文档说明，不是需求条目（FGMC 这类文档尤其明显：
    正文里大段描述总线/信号，但需求条目本身都在表格里）。

    表格本身也要过滤：非需求表（无需求编号字段）、说明性表格（需求编号为 N/A）、
    "是否为需求=否"的表格，都跳过。跳过清单（含原因与表格前的最近一段文字）在 `skipped` 中返回，
    供人工审计"为什么某条需求没出现在结果里"。

    返回 (reqs, skipped)。
    """
    with zipfile.ZipFile(path) as z:
        xml = z.read('word/document.xml').decode('utf-8')

    def para_text(p):
        return ''.join(t.text or '' for t in p.iter(W + 't'))

    def cell_text(tc):
        return ' '.join(para_text(p) for p in tc.iter(W + 'p')).strip()

    root = ET.fromstring(xml)
    body = root.find('w:body', NS)
    reqs, skipped, tbl_no, last_prose = [], [], 0, ''
    for child in body:
        tag = child.tag.split('}')[1]
        if tag == 'p':
            txt = para_text(child).strip()
            if txt:
                last_prose = txt        # 仅用于审计说明（记录表格前的文字），不参与任何提取
        elif tag == 'tbl':
            tbl_no += 1
            raw = {}
            for tr in child.findall(W + 'tr'):
                cells = [cell_text(tc) for tc in tr.findall(W + 'tc')]
                if len(cells) >= 2 and cells[0]:
                    raw[cells[0].strip()] = cells[1].strip()
            kv = normalize_fields(raw)
            rid = kv.get('需求ID', '').strip()
            if not raw:
                reason = '空表'
            elif '需求ID' not in kv:
                reason = '非需求表（无"需求编号/需求ID"字段）'
            elif not rid:
                reason = '需求编号为空或为 N/A 占位（说明性表格）'
            elif kv.get('是否为需求', '').strip().lower() in _NOT_REQ_VALUES:
                reason = '"是否为需求"为否（说明性表格）'
            else:
                reason = None
            if reason:
                skipped.append({
                    'table_no': tbl_no,
                    'reason': reason,
                    'preceding_text': last_prose[:80] or None,
                    'desc': (kv.get('需求中文') or '')[:120] or None,
                })
                continue
            reqs.append({'id': rid, **kv})
    return reqs, skipped


def detect_segments(r):
    """整条需求表格内相关字段 -> [(字段名, 文本), ...]，按 DETECT_FIELD_ORDER 优先排序。
    属性检测与身份提取都基于这些字段；evidence 标注来源字段名。"""
    segs = []
    seen = set()
    for k in DETECT_FIELD_ORDER:
        if k in r and r[k] and r[k].strip():
            segs.append((k, r[k].strip()))
            seen.add(k)
    for k in r:
        if k == 'id' or k in seen or is_meta_field(k):
            continue
        if r[k] and str(r[k]).strip():
            segs.append((k, str(r[k]).strip()))
    return segs


# ---------------- 3.5 信号名提取（多级锚点 + 反向裁剪） ----------------
# 信号名结尾关键词。组合词置于前，保证长匹配优先（"状态消息"不被"状态"截断）。
_NAME_TAIL_SRC = (
    r'(?:状态消息|速度信号|温度信号|故障状态|开关反馈|供电电压回采值|回采值'
    r'|速度指令|工作模式|温度选择值|温度选择字|状态字|状态值|选择值|选择字'
    r'|通信测试信息|测试信息|变化率|架次数'
    r'|信号|反馈|指令|标志|状态|模式|字|值|电压|电流|速度|转速|温度|压力|高度'
    r'|时间|日期|架次|注册号|字符|消息|信息|阶段|密度|流量)')
_NAME_TAIL = re.compile(_NAME_TAIL_SRC)

# 锚点分级（越靠前优先级越高）：
#   1) 中的 / 数据中的 —— 定位句子后段的参数描述（"解析L150的bit11至bit18为UTC时间"）
#   2) 为             —— "bit10至bit28为气压高度" 定义句式
#   3) 动词           —— 写入 / 解析 / 采集 / 发送 / 接收 …
#   4) 将             —— "将风扇速度…写入" 前置语序
_NAME_ANCHORS = [
    ['中的'],
    ['为'],
    ['写入', '读取', '解析', '采集', '发送', '接收', '设为', '置为', '设置为'],
    ['将'],
]

# 反向裁剪边界：候选名字的左边界
# 注意：不收录单字"当"——"当前通道配置状态"这类合法词会被截断；条件前缀改由 _NAME_PREFIX 剥离。
_NAME_CUT = ['发送数据', '接收数据', '发送通道', '接收通道', '发送消息', '接收消息',
             '中的', '数据中', '按照', '通过', '解析', '采集', '读取', '写入',
             '发送', '接收', '设为', '置为', '设置为', '设置', '将', '向', '从', '为', '中', '时', '后',
             '若', '如果', '以及', '并将', '应将', '应在', '对于']

# 锚点后候选的"跨界"检查。只收录多字词——单字（如"时"）会误杀 UTC时间 这类合法名字。
_NAME_CUT_INNER = ['发送数据', '接收数据', '发送通道', '接收通道', '发送消息', '接收消息',
                   '中的', '按照', '通过', '解析', '采集', '读取', '写入',
                   '设为', '置为', '设置为', '设置', '将', '为']

# 候选名字的前导噪声（循环剥离，长词优先）
_NAME_PREFIX = ['软件应', '软件', '应', '其中', '同时', '并且', '并', '且', '该', '其',
                '一次', '一个', '得到', '获取', '离散量', '模拟量',
                '周期发送', '周期采集', '周期接收', '周期解析', '周期',
                '发送数据', '接收数据', '接口数据', '固定数据',
                '到', '出', '入', '至']

# 剥前缀后仍属通用词、不能当信号名的
_NAME_STOP = {'发送数据', '接收数据', '接口数据', '固定数据', '数据', '值', '信号', '标志',
              '状态', '模式', '指令', '反馈', '消息', '状态消息', '有效', '无效', '参数',
              '字', '测试指令', 'ATP测试指令', '数据位', '消息长度', '字节'}

_NAME_PUNCT = '，。；：、（）()\n\r 　"“”‘’'

# ---- 英文信号名（下划线式标识符）识别 ----
# 各系统写法不一致：有的写中文名，有的直接给下划线式英文信号名
# （RAT_HEAT_FAULT / Heater_Group_3_RPDU_ESW_CMD / EDP_TCB_STATUS_005）。
_EN_IDENT = re.compile(r'(?<![A-Za-z0-9_])([A-Za-z][A-Za-z0-9]*(?:_[A-Za-z0-9]+)+)(?![A-Za-z0-9_])')
# 标识符后紧跟这些中文词时，说明它本身才是信号名（如 "RAT_DEPLOYED信号"）
_EN_NAME_TAIL_CN = ('信号', '状态', '指令', '标志', '命令', '反馈', '模式', '值', '数据', '参数')
# 占位符 <xxx>：整段是数据类型/缓存/条件，不是信号名，处理前先去掉尖括号字符
_PLACEHOLDER_RE = re.compile(r'[<>＜＞]')
# 占位符整段（含内容）—— 英文名提取时要把整段剔除，避免把 <MICRO_COMM_A> 这类位号当信号名
_PLACEHOLDER_FULL_RE = re.compile(r'<[^<>]*>|＜[^＜＞]*＞')
# 模板占位标识符（LBL_XXX / AA_XX）：不是真实信号名/标签，需剔除
_PLACEHOLDER_IDENT_RE = re.compile(r'(?:^|_)X{2,}(?:_|$)', re.I)
# 值位置前缀：标识符紧跟这些词说明它是被赋的值，而非信号本身（"将 X 置为 Y" → Y 是值）
_EN_VALUE_PREFIX_RE = re.compile(r'(?:置为|设为|设置为|赋值为|更新为|为)\s*$')


def extract_english_names(text):
    """提取下划线式英文信号名（按出现顺序，已去噪）。

    剔除四类噪声：
      1. 尖括号占位符内的标识符 —— <MICRO_COMM_A>/<RPDU11> 是位号/条件/缓存名，不是信号；
      2. 模板占位标识符 —— LBL_XXX / AA_XX 这类占位符不对应真实信号；
      3. 后接"主应用/软件/分区/模块"的 —— 那是软件/模块名（如 MICRO_COMM主应用分区软件）；
      4. 后接"复合中文名"的 —— 说明英文只是中文名的一部分（如 FWD_CED2供电电压回采值），
         应交给中文规则整体提取，而不是截成 FWD_CED2。

    判据（第 4 类）：标识符后若是**短中文片段且不含信号类别尾词**，视为独立助记符式信号名
    （HSCU 风格："LBL_DIS_00_SYS1_SSM置为SSM_DIS_NO"）；若该小句里含信号类别尾词或较长，
    则判定为"英文前缀 + 中文名"的复合写法，整段交给中文规则。
    另外，"置为/设为/为" 之后的标识符是被赋的值，不作为信号名。
    """
    text = _PLACEHOLDER_FULL_RE.sub(' ', text)
    out = []
    for m in _EN_IDENT.finditer(text):
        name = m.group(1)
        if _PLACEHOLDER_IDENT_RE.search(name):
            continue
        rest = text[m.end():]
        if re.match(r'\s*(?:主应用|应用|软件|分区|模块)', rest):
            continue
        # 值位置："将X置为Y" 里的 Y 是取值，不是信号
        if _EN_VALUE_PREFIX_RE.search(text[:m.start()][-8:]):
            continue
        m2 = re.match(r'\s*([\u4e00-\u9fa5]+)', rest)
        if m2 and not m2.group(1).startswith(_EN_NAME_TAIL_CN):
            frag = m2.group(1)
            # 紧邻中文含信号类别尾词、或本身较长 → 英文只是中文名的一部分（复合写法）
            if _NAME_TAIL.search(frag) or len(frag) > 6:
                continue
        out.append(name)
    return out


def _reverse_cut(text, end):
    """自 end 位置向左反向扫描，遇边界词或标点即停，返回名字候选"""
    start = end
    while start > 0:
        if text[start - 1] in _NAME_PUNCT:
            break
        if any(text[:start].endswith(b) for b in _NAME_CUT):
            break
        start -= 1
    return text[start:end].strip()


def _clean_signal_name(name):
    """剥离前导噪声词与 label 前缀，过滤通用词；不合格返回 None"""
    if not name:
        return None
    s = name.strip('（）() 　')
    changed = True
    while changed and s:
        changed = False
        for p in sorted(_NAME_PREFIX, key=len, reverse=True):
            if s.startswith(p) and len(s) > len(p):
                s = s[len(p):]
                changed = True
                break
    s = re.sub(r'^(?:LABEL|L)\d{1,3}\s*', '', s)   # 剥 label 前缀：L11压调状态字 -> 压调状态字
    s = s.replace('的', '')                        # RFAN的工作模式 -> RFAN工作模式
    s = s.strip('（）() 　')
    if len(s) < 2 or s in _NAME_STOP:
        return None
    return s


def extract_signal_name(text):
    """从需求文本提取信号名；确实没有则返回 None（不臆造、不推断）"""
    # 占位符 <xxx>（数据类型/缓存/条件）不是信号名，去掉尖括号字符后再处理，
    # 否则会出现 "缓存>数据接收至<WWS后货仓1指令" 这类把占位符当名字的脏结果。
    plain = _PLACEHOLDER_RE.sub(' ', text)
    # 句首条件引导字（"当…时，"/"若…，"/"如果…，"）先剥离，条件从句里的名词才是真正的信号名；
    # "当前/当时/中间"这类词里的"当"是构词成分，不能剥（用否定环视区分）。
    plain = re.sub(r'^(?:当|若|如果)(?!前|时|间|中|天|地|然|初)', '', plain)
    # 1) 显式标注："（信号名：风扇速度SPEED）"
    m = re.search(r'信号名\s*[：:]\s*([^\s，。；：、（()）]{2,24})', plain)
    if m:
        v = _clean_signal_name(m.group(1))
        if v:
            return v
    # 2) A429 固定格式字段：需求主干是"将/把 …的 SSM/SDI/Parity 置为…"时，name 取该字段名。
    #    这类需求没有具体物理信号，操作对象就是字组成字段（与 HLR_544"在LABEL的SDI位写入"同一约定）。
    #    例：FGMC"OFP软件应将DISC信号的SSM置为00" → name=SSM（而不是"…信号"这种泛称）。
    m = re.search(r'(?:将|把)[^，。；]{0,30}?的\s*(SDI|SSM|Parity|PARITY)(?![A-Za-z0-9_])', plain)
    if not m:
        m = re.search(r'(?:将|把)\s*(SDI|SSM|Parity|PARITY)(?![A-Za-z0-9_])', plain)
    if m:
        return 'Parity' if m.group(1).lower() == 'parity' else m.group(1).upper()
    # 3) 英文信号名（下划线式标识符）—— 各系统写法差异大，有的直接给英文名
    #    （传原文：函数内部会剔除 <占位符> 整段，避免把 <MICRO_COMM_A> 这类位号当信号名）
    ens = extract_english_names(text)
    if ens:
        return ens[0]
    tail_body = re.compile(r'[^，。；：、\s（）()]{2,40}?' + _NAME_TAIL_SRC)
    # 主句起点：条件从句里也会出现"状态为X"这类表述，句法锚点限定在主句内查找，
    # 避免把条件取值（如"工作模式状态为正常模式"里的"正常模式"）当成信号名。
    m_main = re.search(r'(?:控制)?软件\s*应|应将|应将数据', plain)
    main_start = m_main.start() if m_main else 0
    # 4) 按锚点分级，级别内按出现位置依次尝试
    for level in _NAME_ANCHORS:
        positions = []
        for a in level:
            positions.extend(mm.end() for mm in re.finditer(re.escape(a), plain))
        for pos in sorted(positions):
            if pos < main_start:
                continue
            m = tail_body.match(plain, pos)
            if not m:
                continue
            cand = m.group(0)
            if any(b in cand for b in _NAME_CUT_INNER):
                continue
            v = _clean_signal_name(cand)
            if v:
                return v
    # 5) 兜底：全局扫描尾词后反向裁剪，取"最长"候选
    #    （长短语更具体，如"左翼温度传感器故障状态"优于停在"左翼温度"）
    best = None
    for m in _NAME_TAIL.finditer(plain):
        v = _clean_signal_name(_reverse_cut(plain, m.end()))
        if v and (best is None or len(v) > len(best)):
            best = v
    return best


# ---------------- 3.6 bit 位段解析（0-based 归一 + 连贯合并） ----------------
# 两种写法基准不同，不能一刀切：
#   "第18到28位" —— 中文序数（1-based）。航空 ICD 的 bit 是 0-31，
#                    自然语言的"第N位"即"第 N 个 bit"，转 0-based 需减 1；
#   "bit13至bit28" —— ASCII 写法本身就是 0-based，不减。
# 起始标记：bitN / 第N位。"第N"后面必须跟"位"或连接符（"第18到28位"里"第18"后面是"到"），
# 用前瞻而非直接匹配"位"，这样既能覆盖"第9和10位"这种写法，又不会误匹配"第2阶段"这类序数。
_BIT_SEG_RE = re.compile(
    r'(?:(?<![A-Za-z0-9_])(bit)\s*(\d{1,2})(?![0-9])'
    r'|第\s*(\d{1,2})\s*(?=位|[至到~～—–−和与、-]))'
    r'(?:\s*(?:至|到|-|~|～|—|–|−|和|与|、)\s*'
    r'(?:(?<![A-Za-z0-9_])(bit)\s*(\d{1,2})(?![0-9])|(?:第\s*)?(\d{1,2})\s*位?))?',
    re.I)


def _parse_bit_ranges(text):
    """解析文本中的位段，统一归一到 0-based 区间，并把连贯的位段合并。

    同一条需求里提到的多个位段若数值连贯，说明它们描述的是**同一个连续字段**，
    合并为一个区间（"…的第18到28位，第29位（符号位）…" → 18~29 → 输出 "17-28"）；
    不连贯的位段各自保留（输出逗号分隔）。

    返回：已排序、已合并的区间列表 [(start, end), ...]。
    """
    spans = []
    for m in _BIT_SEG_RE.finditer(text):
        if m.group(1):                          # 起始 "bitN"：本身即 0-based
            a = int(m.group(2))
        else:                                   # 起始 "第N位"：1-based → 减 1
            a = int(m.group(3)) - 1
        b = a
        if m.group(5):                          # 结束 "bitM"
            b = int(m.group(5))
        elif m.group(6):                        # 结束 "第M位" / 裸数字 M（继承起始基准）
            b = int(m.group(6)) - (0 if m.group(1) else 1)
        spans.append((min(a, b), max(a, b)))
    spans.sort()
    merged = []
    for s, e in spans:
        if merged and s <= merged[-1][1] + 1:   # 相邻或重叠 → 视为同一连续字段
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    return [(s, e) for s, e in merged]


def _fmt_bit_ranges(spans):
    """区间列表 → 字符串："17-28" / "8-9,20" / "5"；无区间返回 None。"""
    if not spans:
        return None
    return ','.join(str(s) if s == e else f"{s}-{e}" for s, e in spans)


# ---------------- 4. 身份信息提取（纯文本） ----------------
def extract_identity(text):
    bus = None
    # 总线识别：先认"X总线"这种明确标识（如"A664总线"、"ARINC 429总线"），避免被同一句里的
    # 数据格式描述抢先命中——"按照ARINC429格式…发送至A664总线"应以 A664（承载总线）为准。
    # 兼容 ARINC429 / ARINC 429 / ARINC-429 等写法。
    m_bus = re.search(r'(A429|A825|A664|ARINC[\s\-]?429|AFDX|CAN)\s*总线', text)
    if m_bus:
        bus = 'A429' if m_bus.group(1).upper().startswith('ARINC') else m_bus.group(1)
    elif re.search(r'A825|CAN|风扇CAN', text):
        bus = 'A825'
    elif re.search(r'A429|ARINC[\s\-]?429|429', text):
        bus = 'A429'
    elif re.search(r'A664|AFDX', text):
        bus = 'A664'
    elif re.search(r'模拟量|Analog|ADCIN', text):
        bus = 'Analog'
    elif re.search(r'离散量|Discrete', text):
        bus = 'Discrete'
    # 信号方向：TX 特征优先，RX 特征其次。
    # 注意"接收到ATP测试指令后发送XX"这类需求，主体方向是发送，TX 优先可正确判定；
    # 开头"接收到指令"只是测试触发条件，不作为方向依据。
    # 词表按各系统常见写法收敛（发送/接收/输出/上报/发布/下发/传输/订阅/获取 等）。
    direction = None
    if re.search(r'发送数据|发送通道|向\d*发送|发送周期|发送|写入|置入|输出|上报|发布|下发|传输', text):
        direction = 'TX'
    elif re.search(r'接收数据|接收通道|接收|解析|采集|读取|输入|订阅|获取', text):
        direction = 'RX'
    labels = []
    # 数字型 LABEL 号（L30 / LABEL126 / Label114 / 标号126，大小写不敏感）
    for m in re.finditer(r'(?:LABEL|L|标号)\s*(\d{1,3})(?![\dA-Za-z_])', text, re.I):
        if m.group(1) not in labels:
            labels.append(m.group(1))
    # 名称型标签（LBL_DIS_00_SYS1）：**不是 LABEL 号**，只作为助记名单独记录（label_name），
    # 不写入 label —— 部分系统的 ICD 里标签只有编号，"LBL_*" 只是文档内的助记名，
    # 需求里没有具体编号时 label 必须为 null（不臆造、不用助记名顶替）。
    mnemonics = []
    nm_labels = []
    for m in re.finditer(r'(?<![A-Za-z0-9_])(LBL_[A-Za-z0-9_]+)', text):
        nm = m.group(1).rstrip('_')
        if nm and not _PLACEHOLDER_IDENT_RE.search(nm) and nm not in nm_labels:
            nm_labels.append(nm)
    for nm in nm_labels:
        # 标签的子字段（LBL_X_SSM / LBL_X_Status）不是独立标签，剔除"扩展自其他标签"的项
        if any(nm.startswith(o + '_') for o in nm_labels if o != nm):
            continue
        if nm not in mnemonics:
            mnemonics.append(nm)
    # bit 位段：中文序数"第N位"按 1-based 解释、转 0-based 减 1；"bitN" 本身即 0-based。
    # 同一需求内连贯的位段合并（"第18到28位" + "第29位（符号位）" → 17-28）
    bit = _fmt_bit_ranges(_parse_bit_ranges(text))
    req_name = extract_signal_name(text)
    # 兜底：A429 固定格式字段（SDI/SSM/奇偶校验 Parity）也可作为信号名识别。
    # 仅在没有其他信号名时生效（如"在LABEL的SDI位写入固定数据" -> name=SDI）。
    if req_name is None:
        m_fmt = re.search(r'\b(SDI|SSM|Parity|PARITY)\b', text)
        if m_fmt:
            req_name = m_fmt.group(1)
    # 本条需求列举到的全部信号（下划线式英文名），用于"多信号需求"的自动拆分
    en_names = extract_english_names(text)
    return {'bus': bus, 'direction': direction, 'labels': labels, 'bit': bit,
            'label_name': mnemonics[0] if mnemonics else None,
            'req_name': req_name, 'en_names': en_names}


# ---------------- 5. 属性识别（整表检测，返回 matched/evidence/value） ----------------
def _extract_bit(seg):
    """段内的 bit 位段（口径同身份提取：中文序数减 1、连贯位段合并）。"""
    return _fmt_bit_ranges(_parse_bit_ranges(seg))


def _in_parens(s, pos):
    """判断 pos 位置是否落在某个未闭合的括号内（用于区分"参数表里的 N位"与"第N位"）"""
    for op, cl in (('（', '）'), ('(', ')')):
        i = s.rfind(op, 0, pos)
        if i != -1:
            j = s.find(cl, i + 1)
            if j == -1 or j > pos:
                return True
    return False


# 否定/排除表述（"注释"类字段常见）：在说明"本条需求不做这项处理"，不是属性定义
_NEG_CLAUSE_RE = re.compile(r'不再|不做|不进行|不涉及|不考虑|无需|无须|不用|不需要|没有必要')


def _in_negative_clause(text, pos):
    """pos 所在句子（以 。；;!换行 为界）内是否含否定/排除表述。

    典型场景：FGMC 注释"接收FRDC发来的燃油温度已经按0.25分辨率扩大，
    FGMC此处不再做处理。"——"0.25分辨率"是上游的处理，本条需求明确不做，
    这种"分辨率"不是本条需求的属性，不能命中。
    """
    start = max(text.rfind(c, 0, pos) for c in '。；;!\n') + 1
    ends = [e for e in (text.find(c, pos) for c in '。；;!\n') if e >= 0]
    end = min(ends) if ends else len(text)
    return bool(_NEG_CLAUSE_RE.search(text[start:end]))


def _find_lsb_res(seg):
    """分辨率（LsbRes）命中判定 → (match, value) 或 None。

    兼容"分辨率1RPM"、"分辨率每bit代表1RPM"、"分辨率每bit代表-1V"、"分辨率为0.001"；
    **否定语境下的"分辨率"不算**（见 _in_negative_clause）。
    "bitX是LSB"是位位置描述、非分辨率，不触发。
    """
    m = re.search(r'分辨率(?:\s*每\s*(?:bit|位)\s*(?:代表|对应|为))?\s*[:：为是]?\s*'
                  r'(-?\d+\.?\d*\s*[A-Za-z%/]*)', seg)
    if m and not _in_negative_clause(seg, m.start()):
        return m, (m.group(1).strip() or None)
    # 提到"分辨率"但没给具体数值 → 归入本类、值留空（不臆造）
    m2 = re.search(r'分辨率', seg)
    if m2 and not _in_negative_clause(seg, m2.start()):
        return m2, None
    # "每bit代表X"（没写"分辨率"字样）也算
    m3 = re.search(r'每\s*(?:bit|位)\s*(?:代表|对应)', seg)
    if m3 and not _in_negative_clause(seg, m3.start()):
        return m3, None
    return None


def detect_attrs(segments, direction, req_name=None):
    """segments: [(字段名, 文本), ...]（整条需求表格的相关字段）"""
    attrs = {}

    def hit(name, m, src, value=None):
        attrs.setdefault(name, {'matched': False, 'evidence': [], 'value': None})
        if not m:
            return
        attrs[name]['matched'] = True
        raw = m if isinstance(m, str) else m.group(0).strip()
        ev = (f"[{src}]" + raw)[:60]
        if ev not in attrs[name]['evidence']:
            attrs[name]['evidence'].append(ev)
        if value is not None and attrs[name]['value'] is None:
            attrs[name]['value'] = value

    def scan(seg, src):
        # Label：**只认数字型 label 号**（L30 / LABEL126 / Label114 / 标号126）。
        # 名称型助记标签（LBL_DIS_00_SYS1）不是 LABEL 号，不归入 Label 类
        # （需求里没有具体编号时 label 为 null，助记名另见身份字段 label_name）。
        m = re.search(r'L(?:ABEL)?\s*(\d{1,3})(?![\dA-Za-z_])', seg, re.I)
        hit('Label', m, src, m.group(1) if m else None)
        # BitOffset：bit 位（按方向归 DS/Msg）；"第N位"与 "bitN" 两种写法都算
        m_bit = _BIT_SEG_RE.search(seg)
        bv = _extract_bit(seg)
        if m_bit:
            if direction == 'TX':
                hit('BitOffsetWithinDS', m_bit, src, bv)
            elif direction == 'RX':
                hit('BitOffsetWithinMsg', m_bit, src, bv)
            else:
                hit('BitOffsetWithinDS', m_bit, src, bv)
                hit('BitOffsetWithinMsg', m_bit, src, bv)
        # 周期：RefreshPeriod / TransmissionIntervalMinimum
        # 兼容多种写法："以100ms为周期"、"以100ms的周期"、"周期为100ms"、"100ms周期"、"每1秒解析"
        m1 = re.search(r'以\s*(\d+\s*(?:ms|毫秒|秒))[^。；]*?周期', seg)
        m1b = re.search(r'周期\s*(?:为|是)\s*(\d+\s*(?:ms|毫秒|s|秒))', seg)
        m1c = re.search(r'(\d+\s*(?:ms|毫秒|s|秒))\s*的?\s*周期', seg)
        m2 = re.search(r'每\s*(\d+\s*(?:秒|毫秒|ms))\s*(?:解析|发送|接收|更新|采样)', seg)
        if m1:
            hit('RefreshPeriod', m1, src, m1.group(1))
        elif m1b:
            hit('RefreshPeriod', m1b, src, m1b.group(1))
        elif m1c:
            hit('RefreshPeriod', m1c, src, m1c.group(1))
        elif m2:
            hit('RefreshPeriod', m2, src, m2.group(1))
        else:
            # 兜底：出现"刷新率/刷新周期/周期"即归入该类（无具体数值时 value=None）
            hit('RefreshPeriod', re.search(r'刷新率|刷新周期|周期', seg), src, None)
        m3 = re.search(r'以\s*(\d+\s*(?:ms|毫秒))\s*为周期\s*发送', seg)
        if not m3:
            m3 = re.search(r'最小发送间隔\s*[:：]?\s*(\d+\s*(?:ms|毫秒|s|秒))', seg)
        hit('TransmissionIntervalMinimum', m3, src, m3.group(1) if m3 else None)
        # 逻辑端口活动超时
        mv = re.search(r'活动超时\s*(?:按|为|[:：])?\s*(\d+\s*(?:ms|毫秒|s|秒))', seg)
        hit('ActivityTimeout', re.search(r'活动超时', seg), src, mv.group(1) if mv else None)
        # 消息发布时延
        mv = re.search(r'发布时延\s*(?:不超过|上限为|为|[:：])?\s*(\d+\s*(?:ms|毫秒|s|秒))', seg)
        hit('PublishedLatency', re.search(r'发布时延', seg), src, mv.group(1) if mv else None)
        # 采样周期
        mv = re.search(r'每\s*(\d+\s*(?:ms|毫秒|s|秒))[^，。；]{0,20}?采样', seg)
        hit('SamplePeriod', mv or re.search(r'采样周期', seg), src, mv.group(1) if mv else None)
        # 消息长度
        mv = re.search(r'消息长度\s*[:：]?\s*(\d+\s*(?:字节|Byte|byte|B))', seg)
        hit('MessageSize', mv, src, mv.group(1) if mv else None)
        # 字节偏移（消息内偏移）
        mv = re.search(r'字节偏移\s*[:：]?\s*(\d+)', seg)
        hit('ByteOffsetWithinMsg', mv, src, mv.group(1) if mv else None)
        # 数据集大小
        mv = re.search(r'数据集大小\s*[:：]?\s*(\d+\s*(?:字节|Byte|byte|B))', seg)
        hit('DataSetSize', mv, src, mv.group(1) if mv else None)
        # 参数占位长度：优先"占N位"（"该参数占比1位"/"该标志占1位"），其次"参数长度N"，
        # 最后才用括号参数表里的 "N位"。
        # 不能用裸 "N位" 兜底：会把 FGMC 的 "第30和31位" 误当参数长度。
        mv = re.search(r'占\s*(\d+)\s*(?:位|bit)', seg)
        if not mv:
            mv = re.search(r'(?:参数|数据)\s*(?:占用|长度|位长|位宽)\s*[:：]?\s*(\d+)\s*(?:位|bit)?', seg)
        if not mv:
            for _m in re.finditer(r'(\d{1,3})\s*位', seg):
                if _in_parens(seg, _m.start()):
                    mv = _m
                    break
        hit('ParameterSize', mv, src, mv.group(1) if mv else None)
        # SSM（状态矩阵）：兼容 "SSM=BNR"、"SSM置为00"、"SSM设为SSM_DIS_NO" 等写法
        mv = re.search(r'SSM\s*(?:=|置为|设为|为)\s*([A-Za-z_0-9]+)', seg)
        hit('SSM', mv, src, mv.group(1) if mv else None)
        # SDI
        m = re.search(r'SDI\s*=\s*(\d+)', seg)
        hit('SDIExpected', re.search(r'SDI', seg), src, m.group(1) if m else 'SDI')
        # 量程：FuncRngMax / FuncRngMin（"满量程范围为-32768至32767"、"量程-70~1100" 均识别）
        m = re.search(r'(?:满量程范围|功能范围|量程|范围)\s*[:：]?\s*为?\s*(-?\d+\.?\d*)\s*(?:至|~|—|-)\s*(-?\d+\.?\d*)', seg)
        hit('FuncRngMax', m, src, m.group(2) if m else None)
        hit('FuncRngMin', m, src, m.group(1) if m else None)
        # 单位：Units
        m = re.search(r'单位\s*[:：]?\s*([^，。；、\n]+)', seg)
        if m:
            hit('Units', m, src, m.group(1).strip())
        else:
            m = re.search(r'degrees\s*C|RPM|°C|kPa', seg)
            hit('Units', m, src, m.group(0) if m else None)
        # 数据格式：DataFormatType
        # 不用 \b（中文紧邻英文时词边界失效，如"编码格式为DIS"），改用 ASCII 边界环视：
        # 既保证 "编码格式为DIS" 命中，又避免把 LBL_DIS_00_SYS1 里的 DIS 误当格式词。
        m = re.search(r'(?<![A-Za-z0-9_])(BNR|BCD|DIS|ISO5|SINT|OPAQUE)(?![A-Za-z0-9_])', seg)
        hit('DataFormatType', m, src, m.group(1) if m else None)
        # 离散状态 1/0：涉及 "bitX=1" 归 OneState、"bitX=0" 归 ZeroState；
        # 编码集（0/1 编码定义）归 CodedSet —— 三类独立判定，任一出现即归入对应类。
        m1 = re.search(r'bit\s*\d+\s*=\s*1', seg)
        m0 = re.search(r'bit\s*\d+\s*=\s*0', seg)
        if m1:
            v1 = re.search(r'bit\s*\d+\s*=\s*1[^。；]*?(?:设为|置为|为|→)\s*[“”"]?([^，。；、”"\n]{1,12})', seg)
            hit('OneState', m1, src, v1.group(1).strip() if v1 else '1')
        if m0:
            v0 = re.search(r'bit\s*\d+\s*=\s*0[^。；]*?(?:设为|置为|为|→)\s*[“”"]?([^，。；、”"\n]{1,12})', seg)
            hit('ZeroState', m0, src, v0.group(1).strip() if v0 else '0')
        if m1 or m0:
            # CodedSet：0/1 编码表（完整提取编码组合定义，而非占位符）
            coded = None
            # 模式A：通道位置 -> bit 组合（如 HLR_544："通道位置为1A时，bit8=0，bit9=0"）
            pairsA = []
            for m in re.finditer(r'通道位置为[“"]([^”"]+)[”"]?时[，,]?\s*((?:bit\s*\d+\s*=\s*[01][，,]\s*)*bit\s*\d+\s*=\s*[01])', seg):
                pairsA.append(f"{m.group(2).strip()}，通道位置为{m.group(1)}")
            if pairsA:
                coded = '；'.join(pairsA)
            else:
                # 模式C：状态 -> bit（倒装语序，如 HLR_547："当X为有效时，bit22=1"）
                pairsC = []
                for m in re.finditer(r'当([^，。；]{1,30}?)为[“"]([^”"]+)[”"]?时[，,]?\s*bit\s*(\d+)\s*=\s*([01])', seg):
                    pairsC.append(f"{m.group(1)}为{m.group(2)}→bit{m.group(3)}={m.group(4)}")
                if pairsC:
                    coded = '；'.join(pairsC)
                else:
                    # 模式B：bit 条件 -> 状态（正逻辑，如 HLR_267/278/4276）
                    pairsB = []
                    for m in re.finditer(r'bit\s*(\d+)\s*=\s*([01])[^。；]{0,25}?(?:设为|置为)[“"]?([^”"，。；]{1,12})', seg):
                        pairsB.append(f"bit{m.group(1)}={m.group(2)}→{m.group(3)}")
                    if pairsB:
                        coded = '；'.join(pairsB)
            hit('CodedSet', m1 or m0, src, coded or '0/1编码定义')
        # LSB 分辨率：LsbRes（"bitX是LSB"是位位置描述，非分辨率，不触发；
        # 否定语境的"分辨率"如"不再做处理"也不算——见 _find_lsb_res）
        _lsb = _find_lsb_res(seg)
        if _lsb:
            hit('LsbRes', _lsb[0], src, _lsb[1])
        # 信号名：Name —— 身份信息已提取的优先，否则从本段文本提取
        nm = req_name or extract_signal_name(seg)
        if nm:
            hit('Name', nm, src, nm)
        else:
            # 兜底：A429 固定格式字段（SDI/SSM/Parity）作为信号名
            m_fmt = re.search(r'\b(SDI|SSM|Parity|PARITY)\b', seg)
            if m_fmt:
                hit('Name', m_fmt, src, m_fmt.group(1))

    for fname, ftext in segments:
        scan(ftext, fname)
    return attrs


def clone_attrs(attrs):
    return {k: {'matched': v['matched'], 'evidence': list(v['evidence']), 'value': v.get('value')}
            for k, v in attrs.items()}


# ---------------- 5.5 AI 接口：映射加载 与 变更驱动的自动更新 ----------------
def load_all_maps(args, reqs):
    """加载三个 AI 辅助映射并做一致性校验。
    返回 (name_map, name_map_status, state_map, state_map_status, signal_map, signal_map_status)
    """
    maps, statuses = [], []
    for arg_attr, label, coverage in (('name_map', 'name_map', 'full'),
                                      ('state_map', 'state_map', 'partial'),
                                      ('signal_map', 'signal_map', 'partial')):
        path = getattr(args, arg_attr, None)
        m = {}
        if path:
            if not os.path.exists(path):
                print(f"警告：找不到 {label} 文件 {path}，忽略", file=sys.stderr)
            else:
                m = load_ai_map(path, label)
        maps.append(m)
        statuses.append(check_ai_map(m, reqs, label, coverage=coverage) if m else None)
    return maps[0], statuses[0], maps[1], statuses[1], maps[2], statuses[2]


def run_ai_update(args, reqs, name_st, state_st, signal_st):
    """需求变更时调用 AI 增量重建映射表。

    设计：**失败不中断主流程**——AI 不可用时退回纯正则，聚类结果照常产出。
    返回统计 dict（未执行返回 None）。
    """
    here = os.path.dirname(os.path.abspath(__file__))
    paths = {
        'name': args.name_map or os.path.join(here, 'name_map.json'),
        'state': args.state_map or os.path.join(here, 'state_map.json'),
        'signal': args.signal_map or os.path.join(here, 'signal_map.json'),
    }
    try:
        if here not in sys.path:
            sys.path.insert(0, here)
        from ai_client import AIClient, AIError
        from ai_maps import update_all_maps
    except ImportError as e:
        print(f"警告：AI 模块不可用（{e}），跳过 AI 更新", file=sys.stderr)
        return None

    # 是否确有变更（映射文件缺失也视为需要生成）
    changes = []
    for st in (name_st, state_st, signal_st):
        if st and (st.get('missing') or st.get('stale') or st.get('text_changed')):
            changes.append(st)
    file_missing = [k for k, p in paths.items() if not os.path.exists(p)]
    if not changes and not file_missing and not args.ai_force:
        print("[AI] 映射与需求文档一致，无需更新")
        return {'updated': False}

    client = AIClient.from_config(
        args.ai_config or os.path.join(here, 'ai_config.json'),
        {'base_url': args.ai_base, 'api_key': args.ai_key, 'model': args.ai_model})
    print(f"[AI] 配置：{client.describe()}")

    if not client.enabled and not args.ai_dry_run:
        print("警告：未配置 api_key，跳过 AI 更新（可用 --ai-key、环境变量 HLR_AI_API_KEY，"
              "或在 ai_config.json 中填写）", file=sys.stderr)
        return None

    print("[AI] 按需求变更增量更新映射表"
          + ("（dry-run：只列计划，不调用 AI）" if args.ai_dry_run else ""))
    try:
        stats = update_all_maps(client, reqs, paths,
                                force=args.ai_force,
                                scan_new=not args.no_ai_scan_new,
                                dry_run=args.ai_dry_run,
                                source_doc=os.path.basename(args.docx))
    except AIError as e:
        print(f"警告：AI 更新失败（{e}），继续使用现有映射", file=sys.stderr)
        return None
    except Exception as e:
        print(f"警告：AI 更新异常（{type(e).__name__}: {e}），继续使用现有映射", file=sys.stderr)
        return None

    return {'updated': True, 'dry_run': bool(args.ai_dry_run), 'stats': stats,
            'ai_calls': client.call_count, 'ai_tokens': client.usage_tokens}


def _ai_update_summary(ai_info):
    """压缩 AI 更新统计，避免写入 meta 后体积过大"""
    if not ai_info:
        return None
    if not ai_info.get('updated'):
        return {'updated': False}
    maps = {}
    for kind, st in (ai_info.get('stats') or {}).items():
        maps[kind] = {
            'regenerated': len(st.get('regenerated') or []),
            'stale': len(st.get('stale') or []),
            'missing': len(st.get('missing') or []),
            'text_changed': len(st.get('text_changed') or []),
            'written': st.get('written', 0),
            'kept': st.get('kept', 0),
        }
    return {'updated': True, 'dry_run': ai_info.get('dry_run', False),
            'ai_calls': ai_info.get('ai_calls', 0), 'ai_tokens': ai_info.get('ai_tokens', 0),
            'maps': maps}


# ---------------- 6. 主流程 ----------------
def main():
    args = parse_args()
    if not os.path.exists(args.docx):
        print(f"找不到需求文档: {args.docx}", file=sys.stderr)
        sys.exit(1)
    if not os.path.exists(args.yaml):
        print(f"找不到属性清单: {args.yaml}", file=sys.stderr)
        sys.exit(1)

    attr_order, attr_sources = load_attrs(args.yaml)
    reqs, skipped_tables = parse_docx(args.docx)

    # AI 辅助映射加载（name_map / state_map / signal_map）
    name_map, name_map_status, state_map, state_map_status, signal_map, signal_map_status = \
        load_all_maps(args, reqs)

    # ---- AI 自动更新映射（可选）：需求变更后调 AI 增量重建 ----
    ai_info = None
    if args.ai_update:
        ai_info = run_ai_update(args, reqs, name_map_status, state_map_status, signal_map_status)
        if ai_info and not args.ai_dry_run:
            # 更新后重新加载映射，后续聚类使用最新值
            name_map, name_map_status, state_map, state_map_status, signal_map, signal_map_status = \
                load_all_maps(args, reqs)

    results = []
    for r in reqs:
        segments = detect_segments(r)
        # 身份信息（bus/label/bit/name/direction）只认"需求中文"——这是需求对信号本身的描述；
        # 若并入基本原理/验证方法等字段，会被其他字段的总线字样污染（如模拟量需求被带成 A429）。
        # 属性类检测则用整表字段（segments），见 detect_attrs。
        ident = extract_identity(r['需求中文'])
        # 有效信号名：name_map 优先（含显式 null 表示"AI 确认无信号名"），否则用正则提取
        final_name = name_map[r['id']]['value'] if r['id'] in name_map else ident['req_name']
        # 整表属性检测
        attrs = detect_attrs(segments, ident['direction'], ident['req_name'])
        # AI 辅助识别 Name（需求级）：name_map 覆盖
        if r['id'] in name_map and name_map[r['id']]['value']:
            attrs.setdefault('Name', {'matched': False, 'evidence': [], 'value': None})
            attrs['Name']['matched'] = True
            attrs['Name']['value'] = name_map[r['id']]['value']
            attrs['Name']['evidence'] = ['[AI辅助识别]']
        # AI 辅助识别 OneState/ZeroState：值非 null 强制命中覆盖；值 null 表示该需求不涉及状态定义，强制移除
        sm = state_map.get(r['id'])
        if sm is not None and sm['value'] is not None:
            for sattr in ('OneState', 'ZeroState'):
                if sattr in sm['value']:
                    if sm['value'][sattr] is None:
                        attrs.pop(sattr, None)
                    else:
                        attrs.setdefault(sattr, {'matched': False, 'evidence': [], 'value': None})
                        attrs[sattr]['matched'] = True
                        attrs[sattr]['value'] = sm['value'][sattr]
                        attrs[sattr]['evidence'] = ['[AI辅助识别]']

        # 接口项拆分优先级：
        #   1) signal_map（AI 辅助，信号级拆分，最精确）
        #   2) 自动拆分：需求列举了 ≥2 个英文信号名时（其余系统常见写法："数据组成如下：（1）RAT_HEAT_FAULT；…"）
        #   3) label 级拆分（每条需求的 labels 列表）
        sig_items = signal_map[r['id']]['value'] if r['id'] in signal_map and signal_map[r['id']]['value'] else None
        if not sig_items and not args.no_auto_split:
            # 标识符里若只是把 LABEL 名重复一遍（HSCU 风格"传输LBL_XXX，其内容如下："），
            # 不算独立信号；去重后 ≥2 个才自动按信号拆分。
            labs = set(ident['labels']) | ({ident['label_name']} if ident.get('label_name') else set())
            names = list(OrderedDict.fromkeys(
                nm for nm in (ident.get('en_names') or []) if nm not in labs))
            if len(names) >= 2:
                # 本条需求只有一个标签时，拆出的信号项共享该标签作为身份
                common_lab = ident['labels'][0] if len(ident['labels']) == 1 else None
                sig_items = [{'label': common_lab, 'name': nm, 'bit': None,
                              'direction': ident['direction'], 'auto': True}
                             for nm in names]
        if sig_items:
            for it in sig_items:
                attrs_i = clone_attrs(attrs)
                src_tag = '[信号自动拆分]' if it.get('auto') else '[AI辅助识别]'
                # 接口项级覆盖：label / name / bit
                if it.get('label'):
                    attrs_i.setdefault('Label', {'matched': False, 'evidence': [], 'value': None})
                    attrs_i['Label']['matched'] = True
                    attrs_i['Label']['value'] = it['label']
                    attrs_i['Label']['evidence'] = [src_tag]
                if it.get('name'):
                    attrs_i.setdefault('Name', {'matched': False, 'evidence': [], 'value': None})
                    attrs_i['Name']['matched'] = True
                    attrs_i['Name']['value'] = it['name']
                    attrs_i['Name']['evidence'] = [src_tag]
                if it.get('bit'):
                    # BitOffset 属性类值按接口项 bit 覆盖（信号级粒度，避免整表合成范围）
                    for bo_attr in ('BitOffsetWithinDS', 'BitOffsetWithinMsg'):
                        if bo_attr in attrs_i and attrs_i[bo_attr]['matched']:
                            attrs_i[bo_attr]['value'] = it['bit']
                            attrs_i[bo_attr]['evidence'] = [src_tag]
                # CodedSet 按接口项过滤：只保留该信号/该 bit 对应的编码项（信号级粒度）
                cd = attrs_i.get('CodedSet')
                if cd and cd.get('value') and cd['value'] != '0/1编码定义':
                    keep = [p for p in cd['value'].split('；')
                            if (it.get('bit') and f"bit{it['bit']}" in p) or (it.get('name') and it['name'] in p)]
                    if keep:
                        cd['value'] = '；'.join(keep)
                results.append({
                    'req_id': r['id'], 'req_text': r['需求中文'],
                    'bus': ident['bus'], 'label': it.get('label'), 'bit': it.get('bit'),
                    'direction': it.get('direction') or ident['direction'],
                    'label_name': ident.get('label_name'),
                    'req_name': it.get('name') or final_name, 'attrs': attrs_i,
                })
        else:
            labels = ident['labels'] or [None]
            for lab in labels:
                attrs_i = clone_attrs(attrs)
                if lab and 'Label' in attrs_i:
                    attrs_i['Label']['evidence'] = [f"[需求中文]L{lab}"]
                    attrs_i['Label']['value'] = lab
                results.append({
                    'req_id': r['id'], 'req_text': r['需求中文'],
                    'bus': ident['bus'], 'label': lab, 'bit': ident['bit'],
                    'direction': ident['direction'], 'label_name': ident.get('label_name'),
                    'req_name': final_name, 'attrs': attrs_i,
                })

    # ---------------- 身份信息全空的需求接口项：不保留聚类 ----------------
    # 判据：bus / label / name / bit 全为空 —— 既无法用于后续身份匹配，也聚不出有意义的类
    # （典型：HSCU 里只写"处理 LBL_XXX"这类泛化需求）。不静默丢弃，单独归档供人工复核。
    def _has_identity(x):
        return any(v not in (None, '', []) for v in (x['bus'], x['label'], x['req_name'], x['bit']))

    dropped_items = [x for x in results if not _has_identity(x)]
    results = [x for x in results if _has_identity(x)]
    dropped_by_req = OrderedDict()
    for x in dropped_items:
        dropped_by_req.setdefault(x['req_id'], []).append(x)
    dropped_reqs = [{
        'req_id': rid, 'req_text': items[0]['req_text'], 'item_count': len(items),
        'reason': '身份信息（bus/label/name/bit）全为空，未生成聚类',
    } for rid, items in dropped_by_req.items()]

    # ---------------- 属性类汇总 ----------------
    attr_classes = []
    for attr in attr_order:
        members = [x for x in results if attr in x['attrs'] and x['attrs'][attr]['matched']]
        reqs_in = [{
            'req_id': x['req_id'], 'bus': x['bus'],
            'label': f"L{x['label']}" if x['label'] else None,
            'label_name': x.get('label_name'),
            'name': x['req_name'], 'bit': x['bit'], 'direction': x['direction'],
            'attr_value': x['attrs'][attr].get('value'),
            'evidence': x['attrs'][attr]['evidence'],
        } for x in members]
        attr_classes.append({
            'attribute': attr,
            'sources': attr_sources.get(attr, []),
            'item_count': len(members),
            'req_ids': list(OrderedDict.fromkeys(x['req_id'] for x in reqs_in)),
            'requirements': reqs_in,
        })

    req_view = [{
        'req_id': x['req_id'], 'bus': x['bus'], 'label': x['label'], 'name': x['req_name'],
        'bit': x['bit'], 'direction': x['direction'], 'label_name': x.get('label_name'),
        'attribute_classes': [a for a in attr_order if a in x['attrs'] and x['attrs'][a]['matched']],
        'req_text': x['req_text'],
    } for x in results]

    # ---------------- 需求级去重结果（供后续身份匹配） ----------------
    dedup_view = []
    by_req = OrderedDict()
    for x in results:
        by_req.setdefault(x['req_id'], []).append(x)
    for rid, items in by_req.items():
        buses = list(OrderedDict.fromkeys(i['bus'] for i in items if i['bus']))
        interfaces = []
        seen_if = set()
        for i in items:
            key = (i['label'], i['req_name'], i['bit'], i['direction'])
            if key in seen_if:
                continue
            seen_if.add(key)
            interfaces.append({
                'label': f"L{i['label']}" if i['label'] else None,
                'label_name': i.get('label_name'),
                'name': i['req_name'], 'bit': i['bit'], 'direction': i['direction'],
            })
        attr_union = []
        for a in attr_order:
            if any(a in i['attrs'] and i['attrs'][a]['matched'] for i in items):
                attr_union.append(a)
        dedup_view.append({
            'req_id': rid,
            'bus': buses[0] if len(buses) == 1 else (buses if buses else None),
            'interfaces': interfaces,
            'interface_count': len(interfaces),
            'attribute_classes': attr_union,
            'req_text': items[0]['req_text'],
        })

    # ---------------- 输出 ----------------
    dedup_out = args.dedup_out
    if not dedup_out:
        base = os.path.basename(args.out)
        if '需求聚类结果' in base:                    # xx需求聚类结果.json -> xx需求去重结果.json
            base2 = base.replace('需求聚类结果', '需求去重结果')
        elif '聚类结果' in base:                      # xx聚类结果.json    -> xx需求去重结果.json
            base2 = base.replace('聚类结果', '需求去重结果')
        else:                                         # xx.json           -> xx_去重.json
            base2 = os.path.splitext(base)[0] + '_去重' + os.path.splitext(base)[1]
        dedup_out = os.path.join(os.path.dirname(args.out) or '.', base2)

    # 实际参与检测的字段全集（含优先字段之外的补充扫描字段，如"注释"/"输入数据"）。
    # 早期这里只统计 DETECT_FIELD_ORDER，会让人误以为"注释"没被扫描——
    # 排查属性来源时看不到它，故改为记录真实全集（顺序：优先字段在前，其余按出现顺序）。
    _detected = OrderedDict()
    for k in DETECT_FIELD_ORDER:
        if any(k in r and r[k] and r[k].strip() for r in reqs):
            _detected[k] = None
    for r in reqs:
        for k, _ in detect_segments(r):
            _detected.setdefault(k, None)
    detection_fields = list(_detected)

    out = {
        'meta': {
            'source_doc': os.path.basename(args.docx),
            'req_count': len(reqs),
            'item_count': len(results),
            'dedup_count': len(dedup_view),
            'dropped_req_count': len(dropped_reqs),
            'dropped_item_count': len(dropped_items),
            'skipped_table_count': len(skipped_tables),
            'skipped_tables': skipped_tables,
            'parse_scope': ('仅从 docx 表格提取需求；表格之外的正文段落（章节说明/设备概述等）不参与任何提取；'
                            '非需求表、"是否为需求=否"的说明性表格跳过（见 skipped_tables）'),
            'detection_fields': detection_fields,
            'attribute_count': len(attr_order),
            'attribute_class_count': sum(1 for c in attr_classes if c['item_count'] > 0),
            'name_map_used': bool(name_map),
            'name_map_status': name_map_status,
            'state_map_used': bool(state_map),
            'state_map_status': state_map_status,
            'signal_map_used': bool(signal_map),
            'signal_map_status': signal_map_status,
            'ai_update': _ai_update_summary(ai_info),
            'dedup_out': dedup_out,
            'cluster_by': ('依据需求表格全部相关字段（需求中文/基本原理等）检测属性：明确提及某属性才归入该类，'
                           '不从 ICD/EoICD 填充；一条需求可出现在多个类；涉及 0/1 编码定义归 OneState/ZeroState/CodedSet；'
                           '多信号需求按信号 Name 拆分接口项'),
        },
        'attribute_classes': attr_classes,
        'requirements': req_view,
        'dropped_requirements': dropped_reqs,
    }
    os.makedirs(os.path.dirname(args.out) or '.', exist_ok=True)
    with open(args.out, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    with open(dedup_out, 'w', encoding='utf-8') as f:
        json.dump({'meta': {
            'source_doc': os.path.basename(args.docx),
            'req_count': len(reqs),
            'item_count': len(results),
            'dedup_count': len(dedup_view),
            'dropped_req_count': len(dropped_reqs),
            'dropped_requirements': dropped_reqs,
            'purpose': '需求级去重结果：每条需求唯一，合并其全部接口身份与属性类并集，供后续身份匹配阶段使用',
            'cluster_by': out['meta']['cluster_by'],
        }, 'dedup_requirements': dedup_view}, f, ensure_ascii=False, indent=2)

    print(f"需求 {len(reqs)} 条 / 接口项 {len(results)} 个 / 去重需求 {len(dedup_view)} 条 / "
          f"属性类 {len(attr_order)} 个（非空 {sum(1 for c in attr_classes if c['item_count']>0)}，"
          f"空 {sum(1 for c in attr_classes if c['item_count']==0)}）")
    if skipped_tables:
        print(f"跳过非需求表格 {len(skipped_tables)} 个（表外正文不参与提取）: "
              f"{[(s['table_no'], s['reason']) for s in skipped_tables]}")
    if dropped_reqs:
        print(f"未生成聚类 {len(dropped_reqs)} 条（身份信息全空，已归档到 dropped_requirements）: "
              f"{[d['req_id'] for d in dropped_reqs]}")
    print(f"已输出: {args.out}")
    print(f"去重结果: {dedup_out}")
    if args.debug:
        print()
        for c in attr_classes:
            if c['item_count'] == 0:
                print(f"[{c['attribute']}] （空）")
                continue
            print(f"[{c['attribute']}] 接口项 {c['item_count']} 条: {c['req_ids']}")


if __name__ == '__main__':
    main()
