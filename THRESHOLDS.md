# 固定卷门槛凭证

日班仍按 README.md 报送整数振幅并签发卷。外协复测以固定卷号和非负整数门槛领取部分明细。它需要核验遗漏的位置确实无需复测，并保留全卷条数和峰值用于核账。

## 调用入口

- `publish.receipts.issue_threshold_anchor(catalog, store, volume_id)` 返回该卷的可信锚。首次仅为活卷发锚，须核验对象库实物与 V1 固定卷的完整载荷一致，随后在目录提交中固定该卷的锚。活卷已有锚时按卷号原样重取，重开两库后亦然。首次写入沿 `Catalog.transaction` 提交，目录事务错误原样传播。提交失败时没有新锚，原 V1 卷仍可领取，原号可以重试。
- `review.result.export_threshold(catalog, store, volume_id, threshold)` 返回交付包。导出使用该卷已提交的锚和按 V1 核验的完整载荷。活卷缺已发锚时抛 `LookupError`。允许为每次导出重扫整卷。
- `review.result.verify_threshold(anchor, volume_id, threshold, package)` 仅凭传入的值核验，成功返回核实的业务结果。它无须源库、门户或同一进程的签发状态。卷号或门槛不合、格式或版本无效、正文或统计无法凭可信锚核对时抛 `ValueError`，无部分结果。

两个在线入口沿用 `UnknownVolumeError`、`RetiredVolumeError` 和需要读取载荷时的 `PayloadVerificationError`。退役后拒绝新导出。已取得的锚及正确的包仍可离线核验。卷号沿用 V1 的正式卷号。门槛须为非负整数，布尔值非法，非法门槛抛 `ValueError`。振幅和门槛沿用台架整数单位与精度。

## 交换值与可信来源

锚和值包通过以下 Python 值交换。锚字典含 `volume_id`（正式卷号）、`version`（正整数承诺版本）和 `root`（小写十六进制 SHA-256 摘要）。锚在门槛确定前发行，认证指定卷的原序记录及全卷统计。同内容共用实物的两份正式卷仍按各自卷号绑定。外协通过可信渠道单独取得并保留原锚，再单独取得导出包。导出方不能替换可信锚或另行签发可信声明。可信渠道的建设由现有业务流程负责。

交付包字典含下列业务字段和 `proof`。`proof` 为可保存和传递的 `bytes`，编码与承诺表示由实现定义并说明。

| 字段 | 值 |
| --- | --- |
| `volume_id` | 正式卷号字符串 |
| `threshold` | 本次门槛 |
| `hits` | 按零起原位置升序排列的 `(位置, 带符号原值)` 元组序列，外层为元组 |
| `record_count` | 全卷条数 |
| `peak` | 全卷最大绝对值，空卷为 `None` |

核验成功返回由这五个业务字段组成的字典。命中的判据为 `abs(振幅) >= threshold`。相同原值出现在不同位置时各计一项。包可包含核验所需的幅值界限及承诺材料，未命中原值、完整原帧及其可逆编码不得入包。核验须判断**全部且仅有**的命中与全卷统计，即使交付方删改命中后，保留值及全卷统计仍能各自对上。

## 手算交班

A 和 B 各报送 `[3, -8, 3, 8]`，二者可共用 V1 实物。A 发锚后，门槛 8 的业务结果为 `{'volume_id': 'volume:A', 'threshold': 8, 'hits': ((1, -8), (3, 8)), 'record_count': 4, 'peak': 8}`。同一锚下，门槛 9 的业务结果只有 `threshold` 改为 `9`、`hits` 改为 `()`。门槛 8 的包删掉 `(1, -8)` 后须拒绝，拿 B 的包核验 A 也须拒绝。空卷在门槛 0 下命中为空、计数为 0、峰值为 `None`。`[0, 0]` 在门槛 0 下命中两处、计数为 2、峰值为 0。`threshold_cases.py` 包含可调用的输入、手算结果和在首次目录提交前拒绝写入的夹具。

## 核验与演示

V1 基线可运行 `python3 -B threshold_cases.py baseline`。新增接口完成后，`python3 -B threshold_cases.py verify` 核验原回归和门槛凭证联动，失败须以非零退出。`python3 -B threshold_cases.py demo` 展示实际的重开导出、断库核验和删项拒绝。将新旧链的核验和实际演示接入 `.pointwise-runtime.json`。沿用清单的 `version` 为 `1`、`kind` 为 `cli`，`setup` 是命令参数数组的列表，`verify` 和 `demo` 各为一条命令的参数数组。附简短说明，写清签发与领取共用的承诺对象、证据覆盖不变量及外协如何使用独立取得的锚。
