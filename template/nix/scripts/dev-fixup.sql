-- Dev fixups for a freshly restored prod copy. NOT neutralization — that is
-- `odoo neutralize` (runs every installed module's data/neutralize.sql), which
-- the tooling executes first. This adds what neutralization must not:
-- local admin/admin credentials and requeueing stuck queue jobs so they run
-- against this copy.
-- Shared by `nix run .#download-backup` and worktree-env's wt-restore.sh.
-- Run WITHOUT ON_ERROR_STOP: a statement whose table is missing from the
-- dump errors and is skipped, the rest still apply.
UPDATE res_users SET login='admin', password='$pbkdf2-sha512$25000$zRkjhPD.P0fo3VsrRag1hg$Ybluv8VT4rorEdlO3H88tQ/Yz9s.kZYEhWnIFybVNRTq4VlD6ZrTcn2TXI7R7bdT26SLC4QtIu5njrS9PL96BA', active=true WHERE id=2;
UPDATE queue_job SET state='pending' WHERE state IN ('started','enqueued');

-- Bump the document sequences past the names already in the restored data.
--
-- A prod dump carries documents whose names were IMPORTED rather than drawn
-- from the sequence (a migration from another ERP, a data load), so the
-- sequence can sit far BELOW the highest name already used. The first creates
-- then succeed and the Nth collides with the table's name uniqueness
-- (`stock_picking_name_uniq`) — which reads as a flaky test, because every run
-- advances the sequence by one and only the run that reaches the used range
-- fails.
--
-- Three details this depends on:
--   * the runtime counter of a `standard` sequence is the POSTGRES sequence
--     `ir_sequence_<id zero-padded to 3>` — note the padding, sequence 2 is
--     `ir_sequence_002`. `ir_sequence.number_next` is the value the sequence
--     was CREATED with and is never read at runtime, so writing it changes
--     nothing;
--   * audit by PREFIX across the whole table, never by the document type that
--     owns the sequence: an imported name may have been written under one type
--     while the sequence hangs off another, and several sequences can share a
--     prefix;
--   * prefixes are interpolated (`%(y)s` and friends), so they have to be
--     resolved for today before any name can be compared against them.
--
-- Scope: sequences reachable from a picking type, which is what names both
-- transfers and manufacturing orders. Documents whose table has no name
-- uniqueness (stock.scrap) cannot collide, and are out of scope by
-- construction since their sequences hang off no picking type.
DO $fixup$
DECLARE
    codes    text[][];
    seq      record;
    seq_name text;
    seq_rel  text;
    pfx      text;
    sfx      text;
    tbl      text;
    tbl_max  bigint;
    max_used bigint;
    next_val bigint;
    bumped   int := 0;
BEGIN
    IF to_regclass('public.stock_picking_type') IS NULL THEN
        RETURN;  -- no stock module in this database
    END IF;

    -- Odoo's _interpolation_dict, as PostgreSQL patterns. Every key also
    -- exists as range_<key> and current_<key>; on a restore all three mean
    -- today.
    codes := ARRAY[
        ARRAY['year', to_char(now(), 'YYYY')],
        ARRAY['month', to_char(now(), 'MM')],
        ARRAY['day', to_char(now(), 'DD')],
        ARRAY['y', to_char(now(), 'YY')],
        ARRAY['doy', to_char(now(), 'DDD')],
        ARRAY['woy', to_char(now(), 'WW')],
        ARRAY['weekday', (to_char(now(), 'D')::int - 1)::text],
        ARRAY['h24', to_char(now(), 'HH24')],
        ARRAY['h12', to_char(now(), 'HH12')],
        ARRAY['min', to_char(now(), 'MI')],
        ARRAY['sec', to_char(now(), 'SS')]
    ];

    FOR seq IN
        SELECT s.id, coalesce(s.prefix, '') AS prefix, coalesce(s.suffix, '') AS suffix
          FROM ir_sequence s
         WHERE s.implementation = 'standard'
           -- A date-range sequence draws from ir_sequence_<id>_<range id>
           -- instead: out of scope rather than half-handled.
           AND coalesce(s.use_date_range, false) = false
           AND s.id IN (SELECT sequence_id FROM stock_picking_type
                         WHERE sequence_id IS NOT NULL)
    LOOP
        -- `%03d` pads to three digits and never truncates, while lpad(x, 3)
        -- would CUT a four-digit id (2426 -> '242') and audit an unrelated
        -- sequence object that happens to exist.
        seq_name := 'ir_sequence_' || CASE WHEN seq.id < 100
                                           THEN lpad(seq.id::text, 3, '0')
                                           ELSE seq.id::text END;
        seq_rel := 'public.' || seq_name;
        CONTINUE WHEN to_regclass(seq_rel) IS NULL;

        pfx := seq.prefix;
        sfx := seq.suffix;
        FOR i IN 1 .. array_length(codes, 1) LOOP
            pfx := replace(pfx, '%(' || codes[i][1] || ')s', codes[i][2]);
            pfx := replace(pfx, '%(range_' || codes[i][1] || ')s', codes[i][2]);
            pfx := replace(pfx, '%(current_' || codes[i][1] || ')s', codes[i][2]);
            sfx := replace(sfx, '%(' || codes[i][1] || ')s', codes[i][2]);
            sfx := replace(sfx, '%(range_' || codes[i][1] || ')s', codes[i][2]);
            sfx := replace(sfx, '%(current_' || codes[i][1] || ')s', codes[i][2]);
        END LOOP;
        -- An interpolation code this script does not know: leave the sequence
        -- alone rather than audit against a prefix that never occurs.
        CONTINUE WHEN position('%(' in pfx) > 0 OR position('%(' in sfx) > 0;

        max_used := 0;
        FOREACH tbl IN ARRAY ARRAY['stock_picking', 'mrp_production'] LOOP
            CONTINUE WHEN to_regclass('public.' || tbl) IS NULL;
            EXECUTE format($q$
                SELECT max(num::bigint)
                  FROM (SELECT substr(name, length($1) + 1,
                                      length(name) - length($1) - length($2)) AS num
                          FROM %I
                         WHERE name IS NOT NULL
                           AND length(name) > length($1) + length($2)
                           AND left(name, length($1)) = $1
                           AND right(name, length($2)) = $2) used
                 WHERE num ~ '^[0-9]+$'
            $q$, tbl) INTO tbl_max USING pfx, sfx;
            max_used := GREATEST(max_used, coalesce(tbl_max, 0));
        END LOOP;
        CONTINUE WHEN max_used = 0;

        EXECUTE format('SELECT CASE WHEN s.is_called THEN s.last_value + p.increment_by
                                    ELSE s.last_value END
                          FROM %s s, pg_sequences p
                         WHERE p.schemaname = ''public''
                           AND p.sequencename = %L', seq_rel, seq_name)
           INTO next_val;
        -- Only ever forward: a sequence already past the imported names is the
        -- normal case and must not be rewound.
        CONTINUE WHEN next_val > max_used;

        PERFORM setval(seq_rel::regclass, max_used + 1, false);
        bumped := bumped + 1;
    END LOOP;

    RAISE NOTICE 'sequence bump: % document sequence(s) moved past the restored names', bumped;
END
$fixup$;
