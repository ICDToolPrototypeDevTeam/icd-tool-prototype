## RPDU 特定判定规则（远程配电单元控制器专用）

适用情形：在 RPDU（Remote Power Distribution Unit，远程配电单元）项目背景下，审查软件高层需求（HLR）与 EoICD 接口定义的一致性时，补充以下两条规则：

1. **接收端不比较总线协议标注**：若 ICD Block 的方向为接收端（方向含"接收"、RX 等），则**不比对总线协议标注是否一致**。原因：EoICD 的 Publisher/Subscriber 表格中各个总线协议 Sheet 页描述的是**发送端**的总线协议（ARINC 429 / A825 / A664 等），并未描述接收端的总线协议，因此接收端信号的总线类型标注不可作为不一致的依据。

2. **缓存/发送周期约束**：若 HLR 描述了软件写入缓存或发送的周期，则该周期应**小于等于**对应 ICD 信号的 TransmissionIntervalMinimum 属性值。若 HLR 周期 > ICD 的 TransmissionIntervalMinimum，判定为 inconsistent（周期不满足最小传输间隔要求）。
