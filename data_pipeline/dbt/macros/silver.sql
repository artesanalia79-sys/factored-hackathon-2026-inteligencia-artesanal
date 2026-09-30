{#-
  Helpers shared by the silver models. Every bronze column is a string; silver trims it, types
  it with try_cast (never failing the build on a bad value) and records problems in dq_* flags.
-#}

{# Trimmed string, empty string becomes NULL. #}
{% macro txt(col) -%}
    nullif(trim({{ col }}), '')
{%- endmacro %}

{# Typed value of a trimmed string column; NULL when the cast fails. #}
{% macro typed(col, data_type) -%}
    {%- if data_type == 'boolean' -%}
        case lower({{ col }})
            when 'true' then true when 't' then true when '1' then true
            when 'false' then false when 'f' then false when '0' then false
        end
    {%- else -%}
        try_cast({{ col }} as {{ data_type }})
    {%- endif -%}
{%- endmacro %}

{# `typed(r.col) as col` for each (col, type) pair, comma separated. #}
{% macro typed_columns(casts, alias='r') -%}
    {%- for col, data_type in casts %}
        {{ typed(alias ~ '.' ~ col, data_type) }} as {{ col }}{{ ',' if not loop.last }}
    {%- endfor %}
{%- endmacro %}

{#
  True when any non-null raw value in `casts` did not survive typing. Never NULL. Three cases:
  - the cast failed (typed value is NULL);
  - lossy numeric cast (decimal/integer types): DuckDB rounds silently ('29.996' -> 30.00 as
    decimal(5,2), '4.6' -> 5 as integer), so the typed value is compared with the raw string
    read at decimal(38,18);
  - timestamp with a UTC offset or `Z` suffix: DuckDB drops the offset silently when casting
    to a naive TIMESTAMP, so such raw values are flagged instead of being shifted.
#}
{% macro cast_failed(casts, alias='r') -%}
    coalesce(
    {%- for col, data_type in casts %}
        {%- set raw = alias ~ '.' ~ col %}
        ({{ raw }} is not null and {{ typed(raw, data_type) }} is null)
        {%- if data_type.startswith('decimal') or data_type == 'integer' %}
        or (cast({{ typed(raw, data_type) }} as decimal(38,18))
            <> try_cast({{ raw }} as decimal(38,18)))
        {%- elif data_type == 'timestamp' %}
        or regexp_matches({{ raw }}, '(Z|z|[+-][0-9]{2}:?[0-9]{2})$')
        {%- endif %}
        {{- ' or' if not loop.last }}
    {%- endfor %}
    , false)
{%- endmacro %}

{# True when any documented NOT NULL column is NULL (after typing). #}
{% macro any_null(cols) -%}
    ({%- for col in cols %}{{ col }} is null{{ ' or ' if not loop.last }}{% endfor -%})
{%- endmacro %}

{# SQL literal list for an enum declared in dbt_project.yml vars.enums. #}
{% macro enum_list(name) -%}
    {%- for value in var('enums')[name] -%}
        '{{ value | replace("'", "''") }}'{{ ', ' if not loop.last }}
    {%- endfor -%}
{%- endmacro %}

{# True when any non-null value is outside its enum; pairs are (column, enum name). #}
{% macro invalid_enum(pairs) -%}
    coalesce(
    {%- for col, name in pairs %}
        ({{ col }} is not null and {{ col }} not in ({{ enum_list(name) }}))
        {{- ' or' if not loop.last }}
    {%- endfor %}
    , false)
{%- endmacro %}

{# ISO 3166-1 alpha-2 code from a Spanish/English country name; NULL when unknown. #}
{% macro country_code(col) -%}
    case strip_accents(lower({{ col }}))
        when 'mexico' then 'MX'
        when 'colombia' then 'CO'
        when 'argentina' then 'AR'
        when 'usa' then 'US' when 'united states' then 'US' when 'estados unidos' then 'US'
        when 'brazil' then 'BR' when 'brasil' then 'BR'
        when 'spain' then 'ES' when 'espana' then 'ES'
    end
{%- endmacro %}

{# Deterministic tie-breaker: hash of the full bronze row. #}
{% macro row_hash(alias='source') -%}
    md5(cast({{ alias }} as varchar))
{%- endmacro %}

{#
  Final CTE body: one row per business key, rows without a key excluded, dq_duplicate_count and
  dq_any added. `columns` is the ordered output column list (without the two added columns),
  `flags` the boolean dq_* columns folded into dq_any, `order_by` the freshness ordering.
#}
{% macro dedup_final(relation, key, columns, flags, order_by) -%}
    select
        {%- for col in columns %}
        {{ col }},
        {%- endfor %}
        dq_duplicate_count,
        (dq_duplicate_count > 1
        {%- for flag in flags %} or {{ flag }}{% endfor %}) as dq_any
    from (
        select
            *,
            cast(count(*) over (partition by {{ key }}) as integer) as dq_duplicate_count,
            row_number() over (partition by {{ key }} order by {{ order_by }}, _row_hash) as _rn
        from {{ relation }}
        where {{ key }} is not null
    )
    where _rn = 1
{%- endmacro %}
