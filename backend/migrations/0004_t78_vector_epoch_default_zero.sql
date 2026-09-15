-- 0004 · T78：documents.vector_epoch 默认值 1 → 0，修正「首解析结果代次=2」
--
-- 目的：     代次语义是「该文档当前是第几代」，应从 1 开始。但 upload_document 建行时
--            不显式赋值、走列默认值，而 _parse_document 里是 new_epoch = vector_epoch + 1。
--            默认值 1 ⇒ 首次解析产出 1+1=2，第 1 代（=1）永远没有数据对应，纯属空转。
--            把默认值改成 0，则「上传时=0 → 首解析 0+1=1 → 重解析 1+1=2」,
--            解析逻辑一行不用改，语义自动正确。
-- 影响表：   documents（仅改列默认值，不动任何存量行的值）
-- 适用前提：无。存量行各自的 vector_epoch 保持原值 —— 检索端 epochs 是从 PG 读出
--            直接传给 Milvus 的（task_queue.py:127 → nodes.py:109 → milvus_client.py:121），
--            新旧文档各用各的数字，两边天然对齐，因此**不需要刷历史数据**。
-- 幂等性：   是（SET DEFAULT 可重复执行，无副作用）。
-- 执行：     cd backend && ./.venv/Scripts/python.exe migrations/apply.py 0004_t78_vector_epoch_default_zero.sql
--
-- 回滚：
--   ALTER TABLE documents ALTER COLUMN vector_epoch SET DEFAULT 1;
--   -- 回滚只影响"此后新建的 documents 行"的初始值，存量行不受影响。
--   -- 注意：回滚会让首解析重新产出代次 2，在新文档上重现 T78 想修的现象。

ALTER TABLE documents
    ALTER COLUMN vector_epoch SET DEFAULT 0;