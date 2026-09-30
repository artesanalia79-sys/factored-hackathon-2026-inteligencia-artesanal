{#-
  Helpers shared by the gold models (T6). Gold reads silver only and produces exactly the tables
  and columns of src/bankagent/contracts/serving.py (plus the T16 baselines).
-#}

{#
  VARCHAR[] with the names of the boolean columns in `flags` that are true, sorted; an empty list
  when none is true (never NULL). `alias` qualifies the columns.
#}
{% macro true_flag_names(flags, alias) -%}
    list_sort(list_filter([
        {%- for flag in flags %}
        case when {{ alias }}.{{ flag }} then '{{ flag }}' end{{ ',' if not loop.last }}
        {%- endfor %}
    ], lambda x: x is not null))
{%- endmacro %}

{# SQL CASE mapping `col` through a {source: target} dict var; `default` when nothing matches. #}
{% macro map_values(col, mapping, default) -%}
    case {{ col }}
        {%- for source, target in mapping.items() %}
        when '{{ source | replace("'", "''") }}' then '{{ target }}'
        {%- endfor %}
        else {{ default }}
    end
{%- endmacro %}
