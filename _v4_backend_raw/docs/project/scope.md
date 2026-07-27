# 项目范围

## 项目定位

ICD Tool 是一个本地运行的 CLI 工具。对 EoICD PubSub Excel 表格（Publisher/Subscriber Table）
和软件高层需求（HLR）Word 文档做覆盖性/一致性分析，输出结构化差异报告。

## 输入边界

| 输入 | 格式 | 是否必需 |
|------|------|----------|
| EoICD Publisher Table | `.xlsx` | 是 |
| EoICD Subscriber Table | `.xlsx` | 是 |
| HLR Word 文档 | `.docx` | 是 |

- Publisher 和 Subscriber 可在同一文件（两个 Sheet）或两个独立文件中
- HLR Word 必须包含特定结构的表格（表0 = 术语表，表1+ = 需求表）
- 输入文件存放在 `doc_input_file/` 目录，**禁止修改**

## 输出边界

| 输出 | 格式 | 生成模块 |
|------|------|----------|
| EoICD 条目化需求列表 | JSON | `parsers/eoicd_excel_parser.py` |
| HLR 需求列表 | JSON | `parsers/hlr_word_parser.py` |
| HLR AI 预标注结果 | JSON | `matching/hlr_labeler.py` |
| 匹配结果 | JSON | `matching/candidate_matcher.py`（正向）/ `reverse_matcher.py`（反向） |
| AI 裁判结果 | JSON | `comparison/semantic_judge.py` |
| 差异报告 | JSON | `comparison/report_generator.py` |
| EoICD 条目化清单 | `.xlsx` | `generators/excel_generator.py` |
| 一致性分析报告 | `.docx` | `generators/word_generator.py` |

所有输出文件统一存放在 `backend/output/` 目录下。

## 当前支持

- 正向管线：EoICD → HLR 覆盖性分析（7 维度统一评分）
- 反向管线：HLR → EoICD 覆盖性分析（4 路分类 + Block 匹配）
- 13 个 CLI 子命令，支持按阶段独立运行
- 单文件或双文件 EoICD 解析
- DeepSeek API 用于 AI 预标注和语义裁判

## 当前不支持

- Web UI 或 API 服务
- 实时分析
- 多 LLM 提供商热切换
- 基于 Embedding 的语义召回
- 全量追溯矩阵
- 自动修改 HLR
- 多模型 A/B 裁判

## 范围变更原则

1. 新功能不得破坏现有正向/反向管线
2. 新输入格式应扩展现有解析器，而非重写
3. 新输出格式应尽可能复用现有模型
4. 范围变更应记录为 ADR 存入 `docs/decisions/`
