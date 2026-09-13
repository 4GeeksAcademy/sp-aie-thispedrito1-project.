-- Ticket #DEV-53 — tabla de control de ejecuciones de jobs programados.
--
-- Idempotente (if not exists): scripts/nightly_export.py la ejecuta al
-- arrancar contra Postgres, así que no hace falta lanzarla a mano. También
-- se puede pegar tal cual en el SQL Editor de Supabase.
--
-- job_runs (orquestación nocturna) != reporting.pipeline_runs (fases del ETL).

create table if not exists public.job_runs (
    id uuid primary key default gen_random_uuid(),
    job_name varchar(100) not null,
    target_date date not null,
    status varchar(20) not null,
    started_at timestamptz,
    finished_at timestamptz,
    error_message text,
    created_at timestamptz not null default now(),
    rows_exported integer,
    pipeline_exit_code integer,
    constraint ck_job_runs_status
        check (status in ('pending', 'processing', 'completed', 'failed'))
);

-- Idempotencia por día: "¿existe un completed para (job_name, target_date)?"
create index if not exists ix_job_runs_job_name_target_date
    on public.job_runs (job_name, target_date);

-- El estado processing actúa como lock: como mucho una fila processing por job.
create unique index if not exists uq_job_runs_single_processing
    on public.job_runs (job_name)
    where status = 'processing';
