-- Every agent language must be a mapped ISO 639-1 code (vars.gold_agent_language). An unknown
-- source language name is kept as written by gold_agents_routing and fails here.
select agent_id, lang
from {{ ref('gold_agents_routing') }}, unnest(languages) as u(lang)
where lang not in ('es', 'en', 'pt')
