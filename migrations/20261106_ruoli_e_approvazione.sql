-- Ruoli e approvazione degli account.
--
-- * Tabella `role` con i due ruoli dell'applicazione: admin (gestisce il backoffice) e user.
-- * Ogni utente ha un ruolo e uno stato: chi si registra resta "in_attesa" finche' un admin non lo
--   abilita ("attivo"); un admin puo' bloccarlo in qualsiasi momento ("bloccato").
-- * Gli utenti GIA' registrati restano attivi: nessuno che usa gia' il gestionale viene chiuso fuori.
-- * Gli admin sono alessandromusiopc@gmail.com e andrea@gmail.com. Il ruolo non si cambia
--   dall'applicazione: per aggiungere o togliere un admin si aggiorna users.role_id qui nel database.

create table if not exists public.role (
  id smallint primary key,
  codice text not null unique,
  descrizione text not null
);

insert into public.role (id, codice, descrizione) values
  (1, 'admin', 'Amministratore: abilita, blocca e visualizza gli account degli utenti'),
  (2, 'user', 'Utente del gestionale')
on conflict (id) do nothing;

-- La tabella la legge solo il backend (chiave service_role), mai le chiavi pubbliche.
alter table public.role enable row level security;
revoke all on public.role from anon, authenticated;

-- Le colonne nuove si aggiungono con default 'attivo' e ruolo user, cosi' gli utenti esistenti
-- restano attivi; subito dopo il default di `stato` diventa 'in_attesa' per le registrazioni nuove.
alter table public.users
  add column if not exists role_id smallint not null default 2 references public.role (id),
  add column if not exists stato text not null default 'attivo',
  add column if not exists email text;

alter table public.users drop constraint if exists users_stato_check;
alter table public.users
  add constraint users_stato_check check (stato in ('in_attesa', 'attivo', 'bloccato'));
alter table public.users alter column stato set default 'in_attesa';

-- Copia dell'email (l'originale e' in auth.users): serve alla ricerca nel pannello Admin.
update public.users u
set email = a.email
from auth.users a
where a.id = u.id and u.email is distinct from a.email;

-- Amministratori.
update public.users
set role_id = 1, stato = 'attivo'
where id in (select id from auth.users where lower(email) in ('alessandromusiopc@gmail.com', 'andrea@gmail.com'));

create index if not exists users_stato_idx on public.users (stato);

-- Elenco utenti del pannello Admin, paginato e filtrato nel database.
-- Ricerca: ogni parola deve comparire in nome, cognome, email, partita IVA o indirizzo
-- dell'azienda (% e _ nel testo valgono alla lettera). Prima gli account in attesa, poi i piu'
-- recenti. `in_attesa` e' il numero di account da approvare (indipendente dai filtri).
create or replace function public.admin_utenti_elenco(
  p_q text default null,
  p_stato text default null,
  p_ruolo text default null,
  p_limit integer default 50,
  p_offset integer default 0
) returns json
language sql
stable
set search_path = public
as $$
  with filtrati as (
    select u.id, u.nome, u.cognome, u.email, u.stato, r.codice as ruolo, u.created_at,
           u.company_id, c.partita_iva, c.indirizzo
    from users u
    join role r on r.id = u.role_id
    left join companies c on c.id = u.company_id
    where (p_stato is null or u.stato = p_stato)
      and (p_ruolo is null or r.codice = p_ruolo)
      and not exists (
        select 1
        from regexp_split_to_table(btrim(coalesce(p_q, '')), '\s+') as parola
        cross join lateral (
          select '%' || replace(replace(replace(parola, '\', '\\'), '%', '\%'), '_', '\_') || '%' as modello
        ) m
        where parola <> ''
          and not (
            coalesce(u.nome, '') ilike m.modello
            or coalesce(u.cognome, '') ilike m.modello
            or coalesce(u.email, '') ilike m.modello
            or coalesce(c.partita_iva, '') ilike m.modello
            or coalesce(c.indirizzo, '') ilike m.modello
          )
      )
  ),
  pagina as (
    select *
    from filtrati
    order by (stato = 'in_attesa') desc, created_at desc, id
    limit p_limit offset p_offset
  )
  select json_build_object(
    'totale', (select count(*) from filtrati),
    'in_attesa', (select count(*) from users where stato = 'in_attesa'),
    'elementi', coalesce(
      (select json_agg(p order by (p.stato = 'in_attesa') desc, p.created_at desc, p.id) from pagina p),
      '[]'::json
    )
  );
$$;

-- Restituisce dati di tutte le aziende: la puo' chiamare solo il backend (che verifica che chi
-- chiede sia admin), mai le chiavi pubbliche.
revoke all on function public.admin_utenti_elenco(text, text, text, integer, integer)
  from public, anon, authenticated;
grant execute on function public.admin_utenti_elenco(text, text, text, integer, integer)
  to service_role;

notify pgrst, 'reload schema';
