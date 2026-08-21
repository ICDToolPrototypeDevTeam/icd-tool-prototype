# -*- coding: utf-8 -*-
"""Constants for EoICD Excel parsing: attribute mappings, unit rules, excluded attributes."""

import os
from pathlib import Path

# ——— Attributes excluded from requirement generation ———
EXCLUDED_ATTRS = {
    "Name", "Guid", "FullName", "ATA", "Tag", "Notes",
    "ChangeAuthority", "FCUPortNumber", "EdeAgeMax", "EdeAgeValidation",
}

# ——— English → Chinese attribute name mapping ———
ATTR_CN_MAP = {
    "ComputeTime": "计算时间",
    "Period": "周期",
    "TotalTime": "总时间",
    "ActivityTimeout": "活动超时",
    "RefreshPeriod": "刷新周期",
    "SamplePeriod": "采样周期",
    "TransmissionIntervalMinimum": "最小发送间隔",
    "PublishedLatency": "发布延迟",
    "SysLatencyWCLimit": "系统延迟最坏情况限制",
    "MessageSize": "消息大小",
    "DataSetSize": "数据集大小",
    "MessageOverhead": "消息开销",
    "MessagePad": "消息填充",
    "BitOffsetWithinDS": "数据集内位偏移",
    "BitOffsetWithinMsg": "消息内位偏移",
    "ByteOffsetFSF": "FSF字节偏移",
    "ByteOffsetWithinMsg": "消息内字节偏移",
    "ParameterSize": "参数大小",
    "LsbRes": "最低有效位分辨率",
    "Multiplier": "乘数",
    "DataFormatType": "数据格式类型",
    "DataAvailability": "数据可用性",
    "DataIntegrity": "数据完整性",
    "Units": "单位",
    "Label": "Label号",
    "SDIExpected": "期望SDI",
    "SSM": "符号状态矩阵",
    "FullScaleRngMax": "满量程最大值",
    "FullScaleRngMin": "满量程最小值",
    "FuncRngMax": "功能范围最大值",
    "FuncRngMin": "功能范围最小值",
    "CodedSet": "编码集",
    "OneState": "1态",
    "ZeroState": "0态",
    "OHMSAttribute": "机载维护属性",
    "RDCULabel": "RDCU标号",
    "MemorySize": "内存大小",
    "Hardware": "硬件",
    "MessageCount": "消息计数",
}

# ——— Unit rules: category → (unit_str, set_of_attr_names) ———
UNIT_RULES = [
    ("ms", {
        "ComputeTime", "Period", "TotalTime", "ActivityTimeout",
        "RefreshPeriod", "SamplePeriod", "TransmissionIntervalMinimum",
        "PublishedLatency", "SysLatencyWCLimit",
    }),
    ("Bytes", {
        "MessageSize", "DataSetSize", "MessageOverhead", "MessagePad",
        "ByteOffsetFSF", "ByteOffsetWithinMsg", "MemorySize",
    }),
    ("Bits", {
        "ParameterSize", "BitOffsetWithinMsg", "BitOffsetWithinDS",
    }),
]

UNIT_MAP: dict[str, str] = {}
for unit, names in UNIT_RULES:
    for name in names:
        UNIT_MAP[name] = unit

# ——— Frame structure signal keywords (Rule 9, case-insensitive) ———
_FRAME_SIGNAL_KEYWORDS_RAW = {
    "Label", "SDI", "SSM", "PARITY", "Parity",
    "WordMarker", "Word_Label", "WordLabel",
    "A429Label", "A429_SDI", "A429_SSM", "A429_Parity",
}
FRAME_SIGNAL_KEYWORDS = {kw.lower() for kw in _FRAME_SIGNAL_KEYWORDS_RAW}

# ——— DP attributes to extract for Subscriber RP dp_ref (Rule 8) ———
DP_ATTRIBUTES_FOR_RP = [
    "ParameterSize",
    "BitOffsetWithinDS",
]


def get_chinese_name(attr_name: str) -> str:
    """Return Chinese name for an attribute, falling back to the English name."""
    return ATTR_CN_MAP.get(attr_name, attr_name)


def get_display_name(attr_name: str, from_dp: bool = False) -> str:
    """Format attribute name for description: 中文名（EnglishName）.

    Optionally append （来自对应DP） suffix.
    """
    cn = get_chinese_name(attr_name)
    suffix = "（来自对应DP）" if from_dp else ""
    return f"{cn}（{attr_name}）{suffix}" if cn != attr_name else f"{attr_name}{suffix}"


def get_unit(attr_name: str) -> str | None:
    """Return unit string for an attribute, or None if no unit applies."""
    return UNIT_MAP.get(attr_name)


def is_excluded(attr_name: str) -> bool:
    """Check whether an attribute should be excluded from requirement generation."""
    return attr_name in EXCLUDED_ATTRS


def is_frame_signal(dp_name: str) -> bool:
    """Check if a DP Name is a frame structure signal (Rule 9, case-insensitive).

    Matches exactly or by token split on _ / space / . / -.
    """
    if not dp_name:
        return False
    tokens = set(dp_name.replace(".", " ").replace("-", " ").replace("_", " ").split())
    tokens.add(dp_name)
    lower_tokens = {t.lower() for t in tokens}
    return bool(lower_tokens & FRAME_SIGNAL_KEYWORDS)


import yaml
from dotenv import load_dotenv

# Load .env from backend/ directory.
# V4 迁入 backend/app/v4/ 后，env load path 由原 `_v4_backend_raw/backend/.env`
# 改写为 `backend/.env`（D4 "环境加载块" 允许改写范围）。
_ENV_PATH = Path(__file__).resolve().parent.parent.parent / ".env"
load_dotenv(_ENV_PATH)

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash")
USE_MOCK_LLM = os.getenv("USE_MOCK_LLM", "0") == "1"

# Multi-agent judge providers (Phase 2-3)
JUDGE_PROVIDERS = [
    p.strip()
    for p in os.getenv("JUDGE_PROVIDERS", "deepseek,minimax,qwen").split(",")
    if p.strip()
]

# Matching score weights
MATCH_SCORE_WEIGHTS = {
    "signal_token": 40,
    "bus_interface": 20,
    "attribute_keyword": 10,
    "device": 10,
    "alias": 15,
}
LOW_SCORE_THRESHOLD = 10
DEFAULT_TOP_K = 5
DEFAULT_LIMIT = 0  # 0 = no limit

# BM25 parameters
BM25_K1 = 1.5
BM25_B = 0.75

# ——— Protocol overhead DataFormatType values (not application-layer data) ———
PROTOCOL_DATAFORMATS = {"A429OCTLBL", "A429PARITY", "A429SDI", "A429_SSM_BNR"}

# ——— Unified matching weights (sum = 100) ———
MATCH_WEIGHTS = {
    "bus": 10,
    "label": 20,
    "direction": 10,
    "device": 15,
    "signal": 20,
    "attr_cat": 5,
    "bm25": 20,
}

# ——— Attribute → category mapping ———
ATTR_CATEGORY_MAP = {
    "ComputeTime": "时序", "Period": "时序", "TotalTime": "时序",
    "ActivityTimeout": "时序", "RefreshPeriod": "时序", "SamplePeriod": "时序",
    "TransmissionIntervalMinimum": "时序", "PublishedLatency": "时序",
    "SysLatencyWCLimit": "时序",
    "MessageSize": "大小", "DataSetSize": "大小", "MessageOverhead": "大小",
    "MessagePad": "大小", "MemorySize": "大小",
    "BitOffsetWithinDS": "数据布局", "BitOffsetWithinMsg": "数据布局",
    "ByteOffsetFSF": "数据布局", "ByteOffsetWithinMsg": "数据布局",
    "ParameterSize": "位宽",
    "DataFormatType": "数据格式", "Units": "数据格式",
    "LsbRes": "数据格式", "Multiplier": "数据格式",
    "FullScaleRngMax": "范围", "FullScaleRngMin": "范围",
    "FuncRngMax": "范围", "FuncRngMin": "范围",
    "CodedSet": "编码/状态", "OneState": "编码/状态", "ZeroState": "编码/状态",
    "Label": "Label号",
    "SDIExpected": "SDI",
    "SSM": "SSM",
    "Hardware": "硬件/配置", "MessageCount": "硬件/配置",
    "OHMSAttribute": "硬件/配置", "RDCULabel": "硬件/配置",
    "DataAvailability": "其他", "DataIntegrity": "其他",
}

# ——— Key attributes for ICD consistency judgment (HLR ↔ EoICD) ———
# Only these attributes are sent to the AI judge for reverse matching cases.
# Infrastructure attributes (timing internals, message layout, hardware config)
# are excluded because HLRs don't specify them at ICD level.
REVERSE_KEY_ATTRS = {
    # Data type & format
    "DataFormatType", "Units", "LsbRes", "Multiplier",
    # Range
    "FullScaleRngMax", "FullScaleRngMin", "FuncRngMax", "FuncRngMin",
    # Bit layout
    "BitOffsetWithinDS", "BitOffsetWithinMsg", "ParameterSize",
    # A429 identifiers
    "Label", "SDIExpected", "SSM",
    # Encoding
    "CodedSet", "OneState", "ZeroState",
    # Timing (ICD-visible)
    "Period", "TransmissionIntervalMinimum",
}

# ——— Direction verb tables ———
SEND_VERBS = {"发送", "写入", "输出", "上报", "发布", "驱动", "设置", "控制"}
RECEIVE_VERBS = {"接收", "采集", "解析", "获取", "读取", "监测", "判断", "检测"}

# ——— Signal leaf common-sense aliases ———
SIGNAL_LEAF_ALIASES = {
    "SPEED": ["速度", "转速"],
    "TEMP": ["温度"],
    "STATUS": ["状态"],
    "CMD": ["指令", "命令"],
    "POSITION": ["位置"],
    "PRESSURE": ["压力"],
    "VOLTAGE": ["电压"],
    "CURRENT": ["电流"],
    "FAULT": ["故障"],
    "MODE": ["模式"],
    "POWER": ["电源", "功率"],
    "FAN": ["风扇", "风机"],
    "VALVE": ["阀门"],
    "ANGLE": ["角度"],
    "RATE": ["速率"],
}

# ——— Data type equivalence for reverse matching (IRD ↔ SWHLR) ———
# Maps EoICD DataFormatType values to HLR-equivalent descriptions
DATA_TYPE_EQUIV: dict[str, set[str]] = {
    "BNR": {"bnr", "binary", "二进制数值", "数值", "sint", "analog", "模拟量"},
    "DIS": {"dis", "discrete", "离散", "离散量", "bool", "boolean", "开关", "状态位"},
    "OPAQUE": {"opaque", "不透明", "透传"},
}


def is_data_type_equiv(ird_type: str, hlr_description: str) -> bool:
    """Check if an EoICD DataFormatType is equivalent to an HLR description."""
    ird_upper = ird_type.strip().upper()
    hlr_lower = hlr_description.strip().lower()
    equivalents = DATA_TYPE_EQUIV.get(ird_upper, set())
    return hlr_lower in equivalents


# ——— Unit equivalence for reverse matching (IRD ↔ SWHLR) ———
UNIT_EQUIV: dict[str, set[str]] = {
    "ft": {"ft", "feet", "英尺", "foot"},
    "degrees c": {"degrees c", "℃", "°c", "摄氏度", "deg c", "degc"},
    "degrees": {"degrees", "°", "度", "deg"},
    "ms": {"ms", "毫秒", "millisecond", "milliseconds"},
    "bytes": {"bytes", "byte", "b", "字节"},
    "bits": {"bits", "bit", "位"},
    "v": {"v", "volt", "volts", "伏", "伏特"},
    "ma": {"ma", "毫安", "milliamp"},
    "kg": {"kg", "千克", "公斤", "kilogram"},
    "psi": {"psi", "磅/平方英寸"},
    "%": {"%", "percent", "百分比"},
}


def is_unit_equiv(ird_unit: str, hlr_description: str) -> bool:
    """Check if an EoICD unit is equivalent to an HLR description."""
    ird_lower = ird_unit.strip().lower()
    hlr_lower = hlr_description.strip().lower()
    if ird_lower == hlr_lower:
        return True
    equivalents = UNIT_EQUIV.get(ird_lower, set())
    return hlr_lower in equivalents


# ——— Chinese → English signal keyword mapping ———
# Used in reverse matching to bridge the cross-language gap:
# HLR signal_keywords extracted by the AI labeler are often Chinese
# (e.g. "风扇", "故障"), while ICD signal_family names are English
# (e.g. "AFTEFAN1_HW_FAULT"). This map injects English tokens when
# Chinese keywords are detected, enabling deterministic token overlap.
CN_SIGNAL_KEYWORD_MAP: dict[str, list[str]] = {
    "风扇": ["FAN"],
    "温度": ["TEMP", "TEMPERATURE"],
    "压力": ["PRESS", "PRESSURE"],
    "速度": ["SPEED"],
    "故障": ["FAULT"],
    "过热": ["OVERHEAT"],
    "阀门": ["VALVE", "SOV"],
    "命令": ["CMD", "COMMAND"],
    "电压": ["VOLTAGE"],
    "电流": ["CURRENT"],
    "转速": ["SPEED", "RPM"],
    "位置": ["POSITION"],
    "高度": ["ALTITUDE"],
    "开关": ["SWITCH"],
    "状态": ["STATUS", "STATE"],
    "控制": ["CTRL", "CONTROL"],
    "信号": ["SIG", "SIGNAL"],
    "运行": ["RUN", "OPER", "OPERATIONAL"],
    "限制": ["LIMIT", "LIMITATION"],
    "永久": ["PERMANENT", "PERM"],
    "欠": ["UNDER"],
    "监测": ["MON", "MONITOR"],
    "告警": ["ALARM", "WARNING"],
    "警告": ["ALARM", "WARNING"],
    "关闭": ["CLOSED", "FAILED_CLOSED"],
    "打开": ["OPEN", "FAILED_OPEN"],
    "低压": ["LOW_PRESS", "LOW_PRESSURE"],
    "高压": ["HIGH_PRESS", "HIGH_PRESSURE"],
    "压力高度": ["PRESSURE_ALTITUDE"],
    "引气": ["BLEED"],
    "空调": ["ACS", "AIR_COND"],
    "调节": ["ADJ", "ADJUST"],
    "区域": ["ZONE"],
    "选择": ["SELECT", "SEL"],
    "飞行": ["FLIGHT"],
    "航段": ["LEG"],
    "发动机": ["ENG", "ENGINE"],
    "风扇整流罩": ["FAN_COMPARTMENT"],
    "反推": ["TRV", "THRUST_REVERSER"],
    "滑油": ["OIL"],
    "振动": ["VIB", "VIBRATION"],
    "扭矩": ["TORQUE"],
    "燃油": ["FUEL"],
    "流量": ["FLOW"],
    "功率": ["PWR", "POWER"],
    "负载": ["LOAD"],
    "电源": ["POWER_SUPPLY"],
    "参考": ["REF", "REFERENCE"],
    "基准": ["REF", "REFERENCE"],
    "校验": ["CHECK", "CHK"],
    "校验和": ["CHECKSUM"],
    "计数": ["COUNT", "CNT"],
    "计时": ["TIME", "TIMER"],
    "延迟": ["DELAY"],
    "滤波": ["FILTER"],
    "增益": ["GAIN"],
    "偏移": ["OFFSET"],
    "量程": ["RANGE"],
    "分辨率": ["RESOLUTION"],
    "精度": ["ACCURACY"],
    "滞后": ["HYSTERESIS"],
    "死区": ["DEADBAND"],
    "校准": ["CALIB", "CALIBRATION"],
    "自检": ["BIT", "BUILT_IN_TEST"],
    "离散": ["DISCRETE", "DISC"],
    "模拟": ["ANALOG"],
    "总线": ["BUS"],
    "通道": ["CH", "CHANNEL"],
    "端口": ["PORT"],
    "速率": ["RATE", "BAUD"],
    "帧": ["FRAME"],
    "字": ["WORD"],
    "标签": ["LABEL"],
    "奇偶": ["PARITY"],
    "源": ["SRC", "SOURCE"],
    "目标": ["DST", "DEST", "DESTINATION"],
    "标识": ["ID", "IDENT", "IDENTIFIER"],
    "版本": ["VER", "VERSION"],
    "配置": ["CFG", "CONFIG"],
    "使能": ["EN", "ENABLE"],
    "禁用": ["DIS", "DISABLE"],
    "复位": ["RST", "RESET"],
    "启动": ["START", "STARTUP"],
    "停止": ["STOP"],
    "暂停": ["PAUSE"],
    "继续": ["RESUME", "CONT"],
    "中断": ["INT", "INTERRUPT"],
    "优先级": ["PRI", "PRIORITY"],
    "周期": ["PERIOD", "CYCLE"],
    "频率": ["FREQ", "FREQUENCY"],
    "占空比": ["DUTY_CYCLE", "PWM"],
    "脉宽": ["PULSE_WIDTH", "PW"],
    "计数器": ["COUNTER"],
    "寄存器": ["REG", "REGISTER"],
    "缓冲区": ["BUF", "BUFFER"],
    "队列": ["QUEUE"],
    "堆栈": ["STACK"],
    "指针": ["PTR", "POINTER"],
    "地址": ["ADDR", "ADDRESS"],
    "索引": ["IDX", "INDEX"],
    "数组": ["ARR", "ARRAY"],
    "矩阵": ["MATRIX"],
    "向量": ["VECTOR"],
    "结构体": ["STRUCT"],
    "枚举": ["ENUM"],
    "位域": ["BITFIELD"],
    "掩码": ["MASK"],
    "移位": ["SHIFT"],
    "逻辑": ["LOGIC"],
    "算术": ["ARITH", "ARITHMETIC"],
    "比较": ["CMP", "COMPARE"],
    "阈值": ["THRESH", "THRESHOLD"],
    "上限": ["UPPER", "MAX"],
    "下限": ["LOWER", "MIN"],
    "标称": ["NOMINAL", "NOM"],
    "额定": ["RATED"],
    "最大": ["MAX", "MAXIMUM"],
    "最小": ["MIN", "MINIMUM"],
    "平均": ["AVG", "AVERAGE"],
    "求和": ["SUM", "TOTAL"],
    "差值": ["DIFF", "DIFFERENCE"],
    "绝对值": ["ABS", "ABSOLUTE"],
    "百分比": ["PCT", "PERCENT"],
    "比率": ["RATIO"],
    "系数": ["COEFF", "COEFFICIENT"],
    "因子": ["FACTOR"],
    "常数": ["CONST", "CONSTANT"],
    "变量": ["VAR", "VARIABLE"],
    "参数": ["PARAM", "PARAMETER"],
    "默认": ["DEFAULT", "DEF"],
    "初始": ["INIT", "INITIAL"],
    "正常": ["NORMAL", "NORM"],
    "异常": ["ABNORMAL", "ABNORM"],
    "有效": ["VALID"],
    "无效": ["INVALID"],
    "可用": ["AVAIL", "AVAILABLE"],
    "不可用": ["UNAVAIL", "UNAVAILABLE"],
    "健康": ["HEALTHY", "HEALTH"],
    "降级": ["DEGRADED"],
    "安全": ["SAFETY", "SAFE"],
    "危险": ["HAZARD", "DANGER"],
    "警告": ["WARNING", "WARN"],
    "注意": ["CAUTION"],
    "提示": ["NOTE", "INFO"],
    "紧急": ["EMERGENCY"],
    "关键": ["CRITICAL"],
    "重要": ["IMPORTANT"],
    "次要": ["MINOR"],
    "维修": ["MAINT", "MAINTENANCE"],
    "测试": ["TEST"],
    "诊断": ["DIAG", "DIAGNOSTIC"],
    "记录": ["LOG", "RECORD"],
    "存储": ["STORE", "STORAGE"],
    "加载": ["LOAD"],
    "保存": ["SAVE"],
    "删除": ["DEL", "DELETE"],
    "清除": ["CLR", "CLEAR"],
    "设置": ["SET", "SETTING"],
    "获取": ["GET", "FETCH"],
    "发送": ["SEND", "TX", "TRANSMIT"],
    "接收": ["RECEIVE", "RX", "RECV"],
    "请求": ["REQ", "REQUEST"],
    "响应": ["RSP", "RESPONSE"],
    "确认": ["ACK", "ACKNOWLEDGE"],
    "广播": ["BCAST", "BROADCAST"],
    "轮询": ["POLL"],
    "同步": ["SYNC"],
    "异步": ["ASYNC"],
    "握手": ["HANDSHAKE"],
    "超时": ["TIMEOUT"],
    "重试": ["RETRY"],
    "失败": ["FAIL", "FAILURE"],
    "成功": ["SUCCESS", "OK"],
    "完成": ["DONE", "COMPLETE"],
    "进行中": ["IN_PROGRESS", "PENDING"],
    "等待": ["WAIT", "WAITING"],
    "就绪": ["READY"],
    "忙碌": ["BUSY"],
    "空闲": ["IDLE"],
    "活动": ["ACTIVE"],
    "非活动": ["INACTIVE"],
    "锁定": ["LOCK", "LOCKED"],
    "解锁": ["UNLOCK"],
    "占用": ["OCCUPIED"],
    "释放": ["RELEASE", "FREE"],
    "旁通": ["BYPASS"],
    "隔离": ["ISOLATE", "ISOLATION"],
    "交叉": ["CROSS"],
    "直通": ["DIRECT"],
    "反向": ["REVERSE"],
    "正向": ["FORWARD"],
    "增量": ["INC", "INCREMENT"],
    "减量": ["DEC", "DECREMENT"],
    "步进": ["STEP"],
    "连续": ["CONTINUOUS"],
    "单次": ["SINGLE", "ONESHOT"],
    "多次": ["MULTI", "MULTIPLE"],
    "循环": ["LOOP", "CYCLE"],
    "迭代": ["ITER", "ITERATION"],
    "递归": ["RECURSIVE"],
    "并发": ["CONCURRENT"],
    "并行": ["PARALLEL"],
    "串行": ["SERIAL"],
    "主": ["MAIN", "PRIMARY", "PRI"],
    "从": ["SLAVE", "SECONDARY", "SEC"],
    "备份": ["BACKUP", "BKP"],
    "冗余": ["REDUNDANT", "REDUNDANCY"],
    "切换": ["SWITCH", "SW"],
    "转换": ["CONVERT", "CONV"],
    "映射": ["MAP", "MAPPING"],
    "缩放": ["SCALE", "SCALING"],
    "钳位": ["CLAMP"],
    "饱和": ["SATURATE", "SAT"],
    "积分": ["INTEGRAL", "INTEG"],
    "微分": ["DERIVATIVE", "DERIV"],
    "比例": ["PROPORTIONAL", "PROP"],
    "前馈": ["FEEDFORWARD", "FF"],
    "反馈": ["FEEDBACK", "FB"],
    "闭环": ["CLOSED_LOOP"],
    "开环": ["OPEN_LOOP"],
    "级联": ["CASCADE"],
    "补偿": ["COMPENSATE", "COMP"],
    "校正": ["CORRECT", "CORR"],
    "调节": ["REGULATE", "REG"],
}


# ——— Synonyms loading ———

_SYNONYMS_PATH = Path(__file__).resolve().parent / "synonyms.yaml"


def load_synonyms() -> dict:
    """Load alias mappings from synonyms.yaml. Returns {alias_key: [synonyms]}."""
    if not _SYNONYMS_PATH.exists():
        return {}
    with open(_SYNONYMS_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


# ============================================================================
# HLR 系统类型配置
# ============================================================================

HLR_SYSTEMS: dict[str, dict] = {
    "hvac": {
        "name": "环控系统",
        # 术语表配置
        "glossary_table_index": 0,
        "glossary_cols": 3,
        # 需求表配置
        "requirement_rows": 8,
        # 字段行索引（从0开始，值在列1）
        "field_rows": {
            "requirement_id": 0,
            "content": 1,
            "is_requirement": 2,
            "is_derived": 3,
            "rationale": 4,
            "is_safety_related": 5,
            "verification_method": 6,
            "implementation_method": 7,
        },
        # object_type 固定为"需求"
        "object_type_value": "需求",
        # is_requirement 特殊解析：列1值等于"需求"时为True
        "is_requirement_value": "需求",
    },
    "fuel": {
        "name": "燃油系统",
        # 术语表配置
        "glossary_table_index": 1,
        "glossary_cols": 3,
        # 需求表配置
        "requirement_rows": 13,
        # 字段行索引（从0开始，值在列1）
        "field_rows": {
            "requirement_id": 1,
            "content": 2,
            "is_requirement": 3,
            "is_derived": 3,
            "is_safety_related": 4,
            "rationale": 6,
            "verification_method": 9,
            "implementation_method": None,
        },
        # object_type 固定为"需求"
        "object_type_value": "需求",
        # is_requirement 特殊解析：列1为布尔值
        "is_requirement_is_boolean": True,
    },
    "hscu": {
        "name": "液压系统",
        # HSCU无术语表
        "glossary_table_index": -1,
        "glossary_cols": 3,
        # 需求表配置
        "requirement_rows": 8,
        # 字段行索引（从0开始，值在列1）
        "field_rows": {
            "requirement_id": 0,
            "content": 1,
            "is_requirement": 2,
            "is_derived": 3,
            "rationale": 4,
            "is_safety_related": 5,
            "verification_method": 6,
            "implementation_method": 7,
        },
        # object_type 固定为"需求"
        "object_type_value": "需求",
        # is_requirement 特殊解析：列1值等于"需求"时为True
        "is_requirement_value": "需求",
    },
}


def get_hlr_system_config(system_type: str) -> dict:
    """获取指定系统类型的 HLR 配置"""
    if system_type not in HLR_SYSTEMS:
        raise ValueError(f"Unsupported system type: {system_type}")
    return HLR_SYSTEMS[system_type]


# ============================================================================
# 追溯表系统类型配置
# ============================================================================

TRACEABILITY_SYSTEMS: dict[str, dict] = {
    "hvac": {
        "name": "环控系统",
        # Table 1: 设备需求与系统ICD追溯表
        "trace_table1_filename": "设备需求与系统ICD追溯表.xlsx",
        "trace_table1_sheet_index": 1,
        "trace_table1_start_row": 2,
        "trace_table1_erd_col": 3,
        "trace_table1_icd_col": 7,
        # Table 2: 单模块需求矩阵分析
        "trace_table2_filename": "单模块需求矩阵分析（设备2软件高层）-裁剪.xlsx",
        "trace_table2_sheet_index": 0,
        "trace_table2_start_row": 4,
        "trace_table2_erd_col": 0,
        "trace_table2_hlr_col": 3,
        "trace_table2_module_col": 4,
        "trace_table2_module_skip": "EICD",
    },
    "fuel": {
        "name": "燃油系统",
        # Table 1: 需求与ICD追溯表_FGMC_裁剪.xlsx
        "trace_table1_filename": "需求与ICD追溯表_FGMC_裁剪.xlsx",
        "trace_table1_sheet_index": 3,
        "trace_table1_start_row": 2,
        "trace_table1_erd_col": 3,  # ERD编号在D列
        "trace_table1_icd_col": 7,
        # Table 2: 单模块需求矩阵分析 (设备2软件).xlsx
        "trace_table2_filename": "单模块需求矩阵分析 (设备2软件).xlsx",
        "trace_table2_sheet_index": 0,
        "trace_table2_start_row": 3,
        "trace_table2_erd_col": 0,
        "trace_table2_hlr_col": 3,
        "trace_table2_module_col": 1,
        "trace_table2_module_skip": None,  # Fuel无EICD跳过逻辑
    },
    "hscu": {
        "name": "液压系统",
        # Table 1: 附件1：需求与ICD追溯表 - HSCU-EOICDREVA-1.0.xlsx
        "trace_table1_filename": "附件1：需求与ICD追溯表 - HSCU-EOICDREVA-1.0.xlsx",
        "trace_table1_sheet_index": 1,
        "trace_table1_start_row": 2,
        "trace_table1_erd_col": 3,
        "trace_table1_icd_col": 7,
        # Table 2: 液压-单模块需求矩阵分析-设备2软件.xlsx
        "trace_table2_filename": "液压-单模块需求矩阵分析-设备2软件.xlsx",
        "trace_table2_sheet_index": 0,
        "trace_table2_start_row": 3,
        "trace_table2_erd_col": 0,
        "trace_table2_hlr_col": 3,
        "trace_table2_module_col": 4,
        "trace_table2_module_skip": None,
    },
}


def get_traceability_config(system_type: str) -> dict:
    """获取指定系统类型的追溯表配置"""
    if system_type not in TRACEABILITY_SYSTEMS:
        raise ValueError(f"Unsupported system type: {system_type}")
    return TRACEABILITY_SYSTEMS[system_type]

