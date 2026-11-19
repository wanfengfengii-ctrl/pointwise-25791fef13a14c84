# 澄川测振试验室固定数据档案

日班台架按原序报送整数振幅。发布员交付固定卷号。夜班按卷号复核明细、记录数及最大绝对振幅。档案员登记退役，回收任务在实物失去最后一个活卷引用后清理存储。

## 运行

使用 Python 3.11 和标准库。在项目根目录执行。

```sh
python3 -B check_project.py
python3 -B demo.py
```

`check_project.py` 由控制台提供。也可直接执行 `python3 -B -m unittest discover -s tests -t .`。演示使用 `samples.json` 和两个临时 SQLite 文件，打印已核对的实际结果。日班的 `[3, -8, 3]` 在夜班仍按原序返回，计数为 3，峰值为 8。空卷计数为 0，峰值为空。单条零读数的峰值为 0。

## Python 接口

```python
from objects.execution import run_pending_deletes
from objects.store import initialize_object_store
from publish.catalog import initialize_catalog
from publish.submissions import submit
from retire.dispatch import collect
from retire.volumes import retire_volume
from review.result import read

catalog = initialize_catalog('catalog.sqlite3')
objects = initialize_object_store('objects.sqlite3')
result = submit(catalog, objects, 'station-C', [3, -8, 3])
if result.receipt is not None:
    volume_id = result.receipt.volume_id
    pickup = read(catalog, objects, volume_id)
    retire_volume(catalog, volume_id)
    collect(catalog, objects)
    run_pending_deletes(objects)
    collect(catalog, objects)
```

每个档案使用一对不同的数据库文件，所在目录需已存在。初始化可重复执行，保留两库中的既有数据和用户修改。调用间无长期打开的连接。另一个进程可用同一对文件继续工作。

- `submit(catalog, objects, request_id, records)` 接收非空字符串请求号、有序整数记录。空记录合法，原序、重复项和任意精度整数保留，振幅沿用台架单位。字符串、字节串、映射和集合不作为记录集合接纳。非法记录引发 `InvalidRecordError`，同号异参引发 `RequestConflictError`，分别由 `intake.samples` 和 `intake.requests` 提供。结果的 `state` 为 `pending` 或 `signed`。`signed` 携带固定 `Receipt`，同参重投按原请求继续或返回原回执。存储读回未就绪可返回 `pending`。目录事务失败时传播数据库错误，签发未提交时不产生新成功回执。
- `read(catalog, objects, volume_id)` 返回 `PickupResult`。`records` 是原序整数元组，`record_count` 是条数，`peak` 是最大绝对值或空卷的 `None`。未知卷和已退役卷分别引发 `review.selection.UnknownVolumeError` 和 `review.selection.RetiredVolumeError`。活卷的载荷缺失、校验不一致或无法读取时引发 `objects.integrity.PayloadVerificationError`。
- `retire_volume(catalog, volume_id)` 登记正式卷的单向退役，返回 `RetirementStatus.RETIRED`，重复退役返回同一状态。未知卷引发 `UnknownVolumeError`。原回执保留。提交退役后的新领取请求会得到已退役错误。
- `collect(catalog, objects)` 检查回收资格并推进此次发现的未完成任务，返回 `CollectionStatus` 元组。状态为 `awaiting_dispatch`、`awaiting_confirmation` 或 `completed`。无待处理任务时返回空元组。目录先登记任务再派发。对象端受理而尚未执行时可以暂停；端口不可达时任务留待下次推进。
- `objects.execution.execute_delete(objects, request_id)` 执行指定的已受理删除，`run_pending_deletes(objects)` 推进当次列出的全部待执行删除。`objects.delete_queue.query_delete` 按原请求查询持久状态。同一删除请求重投的目标必须一致。对象端清理和完成记录作为同一事务提交，目录随后通过 `collect` 对账。

## 持久化与数据口径

目录保存冻结报送、固定卷、退役事实和回收任务。对象库保存载荷、逐实物校验值和删除受理及完成记录。卷以固定实物名和内容摘要独立复核。报送接纳以完整规范字节比较同参，领取同时核验对象自存校验值和正式卷固定的摘要。

载荷帧以 `CV`、一字节版本 1 和四字节无符号大端记录数开头。每条记录由一字节符号、四字节无符号大端幅值长度和最短无符号大端幅值组成。符号 0、1、2 分别表示零、正值和负值，零的幅值长度为零。解析拒绝错误版本、非规范整数、截断和尾随字节。

内容相同的不同请求可签成不同卷并共用合格实物。回收任务固定删除请求与实物名。进入回收的实物不再接纳新引用，后来的同内容报送可以使用新实物。清理旧实物后，新卷仍兑现独立明细，旧卷仍为已退役。
