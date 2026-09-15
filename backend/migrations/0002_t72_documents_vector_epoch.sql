-- 0002 · T72：documents 加"块代次"列 vector_epoch
--
-- 目的：     Milvus 的删除在本环境不可靠（delete 只写 tombstone，compaction 无候选段
--            就永远不生效，见踩坑 #61/#68）。于是换思路：重解析时把代次 +1，写入的
--            块带上 metadata["epoch"]，检索时按 (文档, 代次) 成对过滤 —— 旧块哪怕删不掉，
--            也"看不见即不存在"。这一列就是代次的真相源（PG 做真相源，Milvus 只做索引）。
-- 影响表：   documents（新增列 vector_epoch，NOT NULL DEFAULT 1）
-- 适用前提：无（加列，对存量行安全：存量行会拿到默认值 1）。
-- 幂等性：   否（重复执行报 column already exists）。
-- 执行：     cd backend && ./.venv/Scripts/python.exe migrations/apply.py 0002_t72_documents_vector_epoch.sql
--
-- 回滚：
--   ALTER TABLE documents DROP COLUMN IF EXISTS vector_epoch;
--   -- 注意：回滚后检索端 search(epochs=...) 的过滤条件会失去依据，需同步回退代码。
--
-- 备注（2026-09-14 补录）：同 0001，事后补写。模型已同步（server_default=text("1")）。

ALTER TABLE documents
    ADD COLUMN vector_epoch INTEGER NOT NULL DEFAULT 1;
