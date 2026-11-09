-- Assistente WhatsApp di APS Light.
--
-- * whatsapp_numeri: numeri di cellulare autorizzati a usare il bot, collegati a un account del
--   gestionale (piu' numeri per lo stesso account, es. titolare e capisquadra). Un numero puo'
--   essere collegato a un solo account.
-- * whatsapp_messaggi: messaggi ricevuti da Meta. L'id del messaggio e' la chiave: Meta a volte
--   consegna due volte lo stesso messaggio e cosi' lo si elabora una volta sola. Il testo (o la
--   trascrizione del vocale) resta per ricostruire cosa e' stato chiesto in caso di dubbi.
-- * whatsapp_conversazioni: per ogni numero la conversazione in corso con l'assistente (riparte da
--   zero dopo un periodo di inattivita') e il rapportino in attesa di conferma.

create table if not exists public.whatsapp_numeri (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references public.users (id) on delete cascade,
  nome text not null,
  -- Solo cifre, con prefisso internazionale e senza "+", come lo manda Meta (es. 393331234567).
  numero text not null unique check (numero ~ '^[0-9]{8,15}$'),
  created_at timestamptz not null default now()
);
create index if not exists whatsapp_numeri_user_idx on public.whatsapp_numeri (user_id);

create table if not exists public.whatsapp_messaggi (
  wamid text primary key,
  numero text not null,
  user_id uuid references public.users (id) on delete set null,
  tipo text not null,
  testo text,
  created_at timestamptz not null default now()
);
create index if not exists whatsapp_messaggi_numero_data_idx on public.whatsapp_messaggi (numero, created_at desc);

create table if not exists public.whatsapp_conversazioni (
  numero text primary key,
  user_id uuid references public.users (id) on delete cascade,
  -- Messaggi della conversazione con l'assistente, nel formato dell'API di Claude (solo aggiunte
  -- in coda: la cronologia non si modifica mai, si ricomincia una conversazione nuova).
  messaggi jsonb not null default '[]'::jsonb,
  -- Rapportino preparato e mostrato all'utente, in attesa di "Conferma" / "Annulla".
  bozza jsonb,
  aggiornato_at timestamptz not null default now()
);

-- Tabelle usate solo dal backend (chiave service_role), mai dalle chiavi pubbliche.
alter table public.whatsapp_numeri enable row level security;
alter table public.whatsapp_messaggi enable row level security;
alter table public.whatsapp_conversazioni enable row level security;
revoke all on public.whatsapp_numeri, public.whatsapp_messaggi, public.whatsapp_conversazioni from anon, authenticated;

notify pgrst, 'reload schema';
