-- Elenco paginato dei materiali consegnati in un cantiere (pagina DDT), calcolato nel database
-- invece di scaricare tutte le consegne nel browser.
--
-- Restituisce gli id delle consegne della pagina richiesta (dalla piu' recente), il numero totale
-- di consegne e il loro importo complessivo su TUTTO l'elenco filtrato.
-- Filtri: testo cercato (ogni parola deve comparire nel materiale, nel suo codice, nel fornitore o
-- nel numero del documento), fornitore, periodo.

create or replace function public.ddt_consegne_elenco(
  p_company_id uuid,
  p_cantiere_id uuid,
  p_q text default null,
  p_fornitore_id uuid default null,
  p_da date default null,
  p_a date default null,
  p_limit integer default 50,
  p_offset integer default 0
) returns json
language sql
stable
set search_path = public
as $$
  with filtrate as (
    select d.id, d.data_consegna, d.created_at, d.importo_totale
    from ddt_materiali d
    join cantieri c on c.id = d.cantiere_id
    left join articoli_fornitori af on af.id = d.articolo_fornitore_id
    left join fornitori f on f.id = af.fornitore_id
    where c.company_id = p_company_id
      and d.cantiere_id = p_cantiere_id
      and (p_fornitore_id is null or af.fornitore_id = p_fornitore_id)
      and (p_da is null or d.data_consegna >= p_da)
      and (p_a is null or d.data_consegna <= p_a)
      -- Nessuna parola cercata che manchi da tutti i campi. % e _ nel testo valgono alla lettera.
      and not exists (
        select 1
        from regexp_split_to_table(btrim(coalesce(p_q, '')), '\s+') as parola
        cross join lateral (
          select '%' || replace(replace(replace(parola, '\', '\\'), '%', '\%'), '_', '\_') || '%' as modello
        ) m
        where parola <> ''
          and not (
            coalesce(af.descrizione_articolo, '') ilike m.modello
            or coalesce(af.codice_articolo_fornitore, '') ilike m.modello
            or coalesce(f.ragione_sociale, '') ilike m.modello
            or coalesce(d.numero_documento, '') ilike m.modello
          )
      )
  ),
  pagina as (
    select id, data_consegna, created_at
    from filtrate
    order by data_consegna desc, created_at desc, id
    limit p_limit offset p_offset
  )
  select json_build_object(
    'totale', (select count(*) from filtrate),
    'importo_totale', (select coalesce(sum(importo_totale), 0) from filtrate),
    'ids', coalesce(
      (select json_agg(id order by data_consegna desc, created_at desc, id) from pagina),
      '[]'::json
    )
  );
$$;

-- La funzione riceve l'azienda come parametro: deve poterla chiamare solo il backend (chiave
-- service_role), mai le chiavi pubbliche, altrimenti chiunque potrebbe leggere i dati di
-- un'altra azienda passando un altro id.
revoke all on function public.ddt_consegne_elenco(uuid, uuid, text, uuid, date, date, integer, integer)
  from public, anon, authenticated;
grant execute on function public.ddt_consegne_elenco(uuid, uuid, text, uuid, date, date, integer, integer)
  to service_role;

-- Indice per l'elenco di un cantiere ordinato per data, utile quando le consegne diventano migliaia.
create index if not exists ddt_materiali_cantiere_data_idx on public.ddt_materiali (cantiere_id, data_consegna desc);

notify pgrst, 'reload schema';
