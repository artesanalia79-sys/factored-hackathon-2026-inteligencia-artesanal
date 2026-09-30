-- T6: source agent specialty and languages, the inputs of agents_routing.specialty/languages.
-- Aggregates only.
select 'specialty' as field, coalesce(specialty, 'NULL') as value, count(*) as agents
from {{ ref('silver_service_agents') }} group by 1, 2
union all
select 'languages', languages, count(*) from {{ ref('silver_service_agents') }} group by 1, 2
union all
select 'agent_status', agent_status, count(*) from {{ ref('silver_service_agents') }} group by 1, 2
order by 1, 3 desc
