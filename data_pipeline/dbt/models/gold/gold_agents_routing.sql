{#- Serving table agents_routing: every silver agent (inactive ones too, with is_active false).
    specialty maps the Spanish source value through `vars.gold_agent_specialty` (anything else,
    including NULL, is general; nothing maps to cards). languages maps each comma-separated
    Spanish name to ISO 639-1 through `vars.gold_agent_language`; an unknown name is kept as
    written so the singular test `gold_agents_routing_languages_iso` fails loudly. -#}
{%- set language_map = var('gold_agent_language') -%}

with agents as (
    select * from {{ ref('silver_service_agents') }}
),

final as (
    select
        agent_id,
        list_transform(
            string_split(languages, ','),
            lambda x: {{ map_values('lower(trim(x))', language_map, 'lower(trim(x))') }}
        ) as languages,
        {{ map_values('specialty', var('gold_agent_specialty'), "'general'") }} as specialty,
        agent_type,
        country_code as country_of_origin,
        coalesce(agent_status = 'Active', false) as is_active
    from agents
    where languages is not null
        and agent_type is not null
        and country_code is not null
)

select * from final
