--  from https://stackoverflow.com/a/34446754
CREATE OR REPLACE FUNCTION f_trunc_columns(_tbl anyelement, _len int = 80)
  RETURNS SETOF anyelement AS
$func$
DECLARE
   _typ  CONSTANT regtype[] := '{bpchar, varchar}';  -- types to shorten
BEGIN
   RETURN QUERY EXECUTE (
   SELECT format('SELECT %s FROM %s'
               , string_agg(CASE WHEN a.atttypid = 'text'::regtype  -- simple case text
                              THEN format('left(%I, %s)', a.attname, _len)
                            WHEN a.atttypid = ANY(_typ)             -- other short types
                              THEN format('left(%I::text, %s)::%s'
                                 , a.attname, _len, format_type(a.atttypid, a.atttypmod))
                            ELSE quote_ident(a.attname) END         -- rest
                          , ', ' ORDER BY a.attnum)
               , pg_typeof(_tbl))
   FROM   pg_attribute a
   WHERE  a.attrelid = pg_typeof(_tbl)::text::regclass
   AND    NOT a.attisdropped  -- no dropped (dead) columns
   AND    a.attnum > 0        -- no system columns
   );
END
$func$  LANGUAGE plpgsql;
