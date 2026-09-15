-- 0003 · T73：citations 类型统一 + 外键 + 索引
--
-- 目的：     citations.document_id 原来是 varchar（跟 documents.id 的 uuid 对不上），
--            且 citations.kb_id / document_id 都没有外键 -> 孤儿引用在 DB 层约束不了；
--            删文档/删 KB 靠 kb.py 里手工 delete(Citation) 模拟级联，删掉的是
--            **历史报告的引用行**（报告还在、来源却整条消失）。
--            用户决策：删文档 / 删 KB 时【保留引用行、只把指向置空】（ON DELETE SET NULL），
--                      历史报告不会被后来的删除操作掏空。
-- 影响表：   citations（改列类型 + 两个外键 + 两个索引）、documents（一个索引）
-- 适用前提：citations 表为 0 行，或表中 document_id 全部是合法 uuid 字符串
--            （varchar -> uuid 的 USING 转换遇到非法值会整个失败）。
--            2026-09-14 执行前已确认为 0 行。
-- 幂等性：   否。
-- 执行：     cd backend && ./.venv/Scripts/python.exe migrations/apply.py 0003_t73_citations_fk.sql
--
-- 回滚：
--   DROP INDEX IF EXISTS ix_citations_document_id, ix_citations_report_id, ix_documents_kb_id;
--   ALTER TABLE citations DROP CONSTRAINT IF EXISTS citations_kb_id_fkey;
--   ALTER TABLE citations DROP CONSTRAINT IF EXISTS citations_document_id_fkey;
--   ALTER TABLE citations ALTER COLUMN document_id TYPE varchar USING document_id::varchar;
--
-- 配套代码改动（本项目里"迁移"和"代码"必须同一批发布，缺一不可）：
--   · models/__init__.py：Citation 的两个外键 + 三个索引；Document.kb_id 加 index
--   · api/v1/endpoints/kb.py：删掉 delete_kb / delete_document 里两处手工 delete(Citation)
--   · api/v1/endpoints/kb.py 的 reparse_document 仍保留手工删引用（用户明确选择现状）

\echo '① document_id: varchar -> uuid'
ALTER TABLE citations ALTER COLUMN document_id TYPE uuid USING document_id::uuid;

\echo '② 两个外键（ON DELETE SET NULL：引用行保留，只断链接）'
ALTER TABLE citations
  ADD CONSTRAINT citations_document_id_fkey
  FOREIGN KEY (document_id) REFERENCES documents(id) ON DELETE SET NULL;

ALTER TABLE citations
  ADD CONSTRAINT citations_kb_id_fkey
  FOREIGN KEY (kb_id) REFERENCES knowledge_bases(id) ON DELETE SET NULL;

\echo '③ 三个索引（外键列 Postgres 不会自动建）'
CREATE INDEX ix_documents_kb_id       ON documents(kb_id);
CREATE INDEX ix_citations_report_id   ON citations(report_id);
CREATE INDEX ix_citations_document_id ON citations(document_id);

\echo '=== 迁移后 citations ==='
\d citations
