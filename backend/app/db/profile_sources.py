"""Remove document/page citation metadata while retaining company profile values."""

from psycopg import AsyncConnection, sql


async def remove_profile_sources(conn: AsyncConnection, company_id: str) -> None:
    cur = await conn.execute("SELECT id FROM profil.deal WHERE projektname=%s", (company_id,))
    deal = await cur.fetchone()
    await conn.execute(
        "UPDATE pipeline.client_info SET citations='{}'::jsonb WHERE company_id=%s", (company_id,)
    )
    if deal is None:
        return
    deal_id = deal[0]
    cur = await conn.execute("SELECT id FROM profil.gesellschaft WHERE deal_id=%s", (deal_id,))
    ids = [row[0] for row in await cur.fetchall()]
    cur = await conn.execute("SELECT id FROM profil.dokument WHERE deal_id=%s", (deal_id,))
    documents = [row[0] for row in await cur.fetchall()]
    cur = await conn.execute("""SELECT table_name, array_agg(column_name::text)
        FROM information_schema.columns WHERE table_schema='profil'
        AND table_name IN (SELECT table_name FROM information_schema.tables
            WHERE table_schema='profil' AND table_type='BASE TABLE') GROUP BY table_name""")
    for table, columns in await cur.fetchall():
        cleared = [name for name in ("quelle_dok", "fundstelle") if name in columns]
        if not cleared:
            continue
        if "deal_id" in columns:
            scope, params = sql.SQL("deal_id=%s"), (deal_id,)
        elif "gesellschaft_id" in columns:
            scope, params = sql.SQL("gesellschaft_id=ANY(%s)"), (ids,)
        elif "aussenpruefung_id" in columns:
            scope = sql.SQL(
                "aussenpruefung_id IN (SELECT id FROM profil.aussenpruefung "
                "WHERE gesellschaft_id=ANY(%s))"
            )
            params = (ids,)
        else:
            owners = [
                name
                for name in (
                    "organgesellschaft_id",
                    "organtraeger_id",
                    "darlehensnehmer_id",
                    "eigentuemer_id",
                    "nutzer_id",
                )
                if name in columns
            ]
            if owners:
                scope = sql.SQL(" OR ").join(
                    sql.SQL("{}=ANY(%s)").format(sql.Identifier(name)) for name in owners
                )
                params = tuple(ids for _ in owners)
            else:
                scope, params = sql.SQL("quelle_dok=ANY(%s)"), (documents,)
        assignments = sql.SQL(", ").join(
            sql.SQL("{}=NULL").format(sql.Identifier(name)) for name in cleared
        )
        await conn.execute(
            sql.SQL("UPDATE profil.{} SET {} WHERE {}").format(
                sql.Identifier(table), assignments, scope
            ),
            params,
        )
    await conn.execute(
        """UPDATE profil.pruefhinweis SET quelle_a=NULL, quelle_b=NULL,
        beschreibung=CASE WHEN art='konflikt'
            THEN 'Abweichende Werte im Profil übernommen.' ELSE beschreibung END,
        entscheidung=CASE WHEN art='konflikt'
            THEN 'Profil gespeichert; Änderungen im Verlauf.' ELSE entscheidung END
        WHERE gesellschaft_id=ANY(%s)""",
        (ids,),
    )
    await conn.execute("DELETE FROM profil.dokument WHERE deal_id=%s", (deal_id,))
