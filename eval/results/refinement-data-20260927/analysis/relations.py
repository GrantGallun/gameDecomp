import sqlite3
db=sqlite3.connect("file:campaign.sqlite?mode=ro",uri=True)
for r in db.execute("""select e.relation, count(*), sum(c.exact),
  sum(c.strategy like '%historical-seed%' or c.strategy like '%history-recovery%' or c.strategy like '%historical-provenance%' or c.strategy like '%symbol-restoration%')
 from attempt_edges e join attempts p on p.id=e.parent_attempt_id join attempts c on c.id=e.child_attempt_id
 where p.compiled=1 and c.compiled=1 and c.score>p.score group by 1 order by 2 desc"""): print(r)
