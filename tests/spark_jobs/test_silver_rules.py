"""Tests de las reglas del contrato de datos (no requieren Spark ni JVM)."""

from __future__ import annotations

import pytest

from pipelines.reservas.parser import LONG_COLUMNS
from pipelines.spark_jobs.silver_rules import (
    Contract,
    ContractColumn,
    Measures,
    RunReport,
    cast_expression,
    column_names,
    contract_names,
    dq_runs_table,
    hard_failures,
    load_contract,
    missing_columns,
    pending_resources,
    reject_reason_expression,
    reject_rules,
    rejects_table,
    required_columns,
    run_status,
    select_expressions,
    sql_string,
    too_many_rejects,
)


def columna(name: str, tipo: str = "string", **extra) -> ContractColumn:
    return ContractColumn(
        name=name,
        type=tipo,
        nullable=extra.get("nullable", True),
        description="",
        minimum=extra.get("minimum"),
        maximum=extra.get("maximum"),
        allowed_values=tuple(extra.get("allowed_values", ())),
    )


def contrato(*columnas: ContractColumn) -> Contract:
    return Contract(
        name="prueba",
        table="lake.silver.prueba",
        source="lake.bronze.prueba",
        primary_key=("idpozo",),
        partition_by=("anio",),
        dedupe_by=None,
        columns=columnas,
    )


# --- parseo del contrato ---------------------------------------------------------------


def test_el_contrato_de_produccion_se_lee_completo():
    contract = load_contract("produccion_pozo")
    assert contract.table == "lake.silver.produccion_pozo"
    assert contract.source == "lake.bronze.produccion_pozo"
    assert contract.primary_key == ("idpozo", "anio", "mes")
    assert contract.partition_by == ("anio",)
    assert contract.dedupe_by == "fechaingreso"
    assert len(contract.columns) == 38


def test_el_contrato_lleva_el_sufijo_del_ambiente_en_las_dos_puntas():
    # El YAML nombra `lake.silver.fractura`; la base real es `silver_dev` (ADR 0005).
    contract = load_contract("fractura", suffix="_dev")
    assert contract.table == "lake.silver_dev.fractura"
    assert contract.source == "lake.bronze_dev.fractura"
    # Las tablas derivadas salen de `contract.table`, así que heredan el sufijo solas.
    assert dq_runs_table(contract) == "lake.silver_dev.dq_runs"


def test_los_tipos_declarados_son_los_esperados():
    columnas = {column.name: column.type for column in load_contract("produccion_pozo").columns}
    assert columnas["idpozo"] == "bigint"
    assert columnas["anio"] == "int"
    assert columnas["prod_pet"] == "double"
    assert columnas["rectificado"] == "boolean"
    assert columnas["fechaingreso"] == "timestamp"
    assert columnas["fecha_data"] == "date"
    assert columnas["empresa"] == "string"


def test_las_columnas_obligatorias_del_contrato():
    contract = load_contract("produccion_pozo")
    assert required_columns(contract) == ["anio", "mes", "idpozo", "empresa"]


def test_los_rangos_declarados_se_leen():
    columnas = {column.name: column for column in load_contract("produccion_pozo").columns}
    assert (columnas["tef"].minimum, columnas["tef"].maximum) == (0, 744)
    assert (columnas["mes"].minimum, columnas["mes"].maximum) == (1, 12)
    assert columnas["prod_pet"].minimum == 0
    assert "NO CONVENCIONAL" in columnas["tipo_de_recurso"].allowed_values


def test_el_contrato_del_padron_no_tiene_dedupe_ni_rangos_de_fecha():
    contract = load_contract("pozo_primera_produccion")
    assert column_names(contract) == ["idpozo", "anio", "mes"]
    assert contract.primary_key == ("idpozo",)
    assert contract.dedupe_by is None


@pytest.mark.parametrize("nombre", contract_names())
def test_cada_contrato_del_repo_se_lee_y_valida(nombre):
    # `load_contract` corre `check_contract`: si un YAML queda mal escrito, salta acá y no
    # a mitad del job en Glue.
    contract = load_contract(nombre)
    assert contract.name == nombre
    assert contract.columns


def test_contract_names_lista_los_yaml_disponibles():
    assert contract_names() == [
        "fractura",
        "pozo_primera_produccion",
        "produccion_pozo",
        "reservas",
    ]


def test_un_tipo_invalido_se_rechaza(tmp_path):
    (tmp_path / "malo.yaml").write_text(
        "table: lake.silver.x\nsource: lake.bronze.x\nprimary_key: [a]\n"
        "columns:\n  - name: a\n    type: decimal\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="tipo invalido"):
        load_contract("malo", tmp_path)


def test_una_clave_primaria_que_no_esta_en_columns_se_rechaza(tmp_path):
    (tmp_path / "malo.yaml").write_text(
        "table: lake.silver.x\nsource: lake.bronze.x\nprimary_key: [b]\n"
        "columns:\n  - name: a\n    type: string\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="no esta en columns"):
        load_contract("malo", tmp_path)


def test_una_clave_primaria_nullable_se_rechaza(tmp_path):
    (tmp_path / "malo.yaml").write_text(
        "table: lake.silver.x\nsource: lake.bronze.x\nprimary_key: [a]\n"
        "columns:\n  - name: a\n    type: string\n    nullable: true\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="no puede ser nullable"):
        load_contract("malo", tmp_path)


def test_columnas_repetidas_se_rechazan(tmp_path):
    (tmp_path / "malo.yaml").write_text(
        "table: lake.silver.x\nsource: lake.bronze.x\nprimary_key: [a]\n"
        "columns:\n  - name: a\n    type: string\n  - name: a\n    type: int\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="columnas repetidas"):
        load_contract("malo", tmp_path)


# --- expresiones de casteo -------------------------------------------------------------


def test_cast_de_string_solo_recorta():
    assert cast_expression(columna("empresa")) == "nullif(trim(`empresa`), '')"


def test_cast_numerico_usa_try_cast():
    assert cast_expression(columna("prod_pet", "double")) == (
        "try_cast(nullif(trim(`prod_pet`), '') AS DOUBLE)"
    )


def test_cast_de_fecha_y_timestamp():
    assert "AS DATE)" in cast_expression(columna("fecha_data", "date"))
    assert "AS TIMESTAMP)" in cast_expression(columna("fechaingreso", "timestamp"))


def test_cast_de_booleano_traduce_t_y_f():
    expresion = cast_expression(columna("rectificado", "boolean"))
    assert expresion == (
        "CASE lower(nullif(trim(`rectificado`), '')) WHEN 't' THEN true WHEN 'f' THEN false END"
    )


def test_select_expressions_respeta_el_orden_y_aliasa():
    contract = contrato(columna("idpozo", "bigint"), columna("empresa"))
    assert select_expressions(contract) == [
        "try_cast(nullif(trim(`idpozo`), '') AS BIGINT) AS `idpozo`",
        "nullif(trim(`empresa`), '') AS `empresa`",
    ]


# --- reglas de rechazo -----------------------------------------------------------------


def test_sin_rangos_no_hay_reglas():
    contract = contrato(columna("empresa"))
    assert reject_rules(contract) == []
    assert reject_reason_expression(contract) == "''"


def test_regla_de_minimo_y_maximo():
    contract = contrato(columna("tef", "double", minimum=0, maximum=744))
    condiciones = [condicion for condicion, _ in reject_rules(contract)]
    motivos = [motivo for _, motivo in reject_rules(contract)]
    assert condiciones == [
        "try_cast(nullif(trim(`tef`), '') AS DOUBLE) < 0",
        "try_cast(nullif(trim(`tef`), '') AS DOUBLE) > 744",
    ]
    assert motivos == ["tef menor que 0", "tef mayor que 744"]


def test_regla_de_allowed_values():
    contract = contrato(columna("tipo", allowed_values=["CONVENCIONAL", "NO CONVENCIONAL"]))
    condicion, motivo = reject_rules(contract)[0]
    assert condicion == ("nullif(trim(`tipo`), '') NOT IN ('CONVENCIONAL', 'NO CONVENCIONAL')")
    assert motivo == "tipo fuera de allowed_values"


def test_reject_reason_junta_todas_las_reglas():
    contract = contrato(
        columna("mes", "int", minimum=1, maximum=12),
        columna("prod_pet", "double", minimum=0),
    )
    expresion = reject_reason_expression(contract)
    assert expresion.startswith("concat_ws('; ', CASE WHEN ")
    assert expresion.count("CASE WHEN") == 3
    assert "'mes menor que 1'" in expresion


def test_sql_string_escapa_la_comilla():
    assert sql_string("O'Higgins") == "'O''Higgins'"


# --- checks de esquema y pendientes ----------------------------------------------------


def test_missing_columns_detecta_las_que_faltan_en_bronze():
    contract = contrato(columna("idpozo", "bigint"), columna("empresa"))
    assert missing_columns(contract, ["idpozo", "_resource_id"]) == ["empresa"]


def test_missing_columns_vacio_si_bronze_las_tiene_todas():
    contract = contrato(columna("idpozo", "bigint"))
    assert missing_columns(contract, ["idpozo", "otra"]) == []


def test_el_contrato_de_reservas_pide_las_columnas_que_escribe_bronze():
    # Bronze escribe la fila larga del parser: si el contrato nombra otra columna, el job
    # silver frena en el check duro de esquema recién cuando corre en Glue.
    assert missing_columns(load_contract("reservas"), list(LONG_COLUMNS)) == []


def test_pending_resources_incluye_lo_nuevo_y_lo_cambiado():
    bronze = {"a": "sha-a", "b": "sha-nueva", "c": "sha-c"}
    silver = {"a": "sha-a", "b": "sha-vieja"}
    assert pending_resources(bronze, silver) == ["b", "c"]


def test_pending_resources_vacio_si_silver_esta_al_dia():
    shas = {"a": "sha-a", "b": "sha-b"}
    assert pending_resources(shas, shas) == []


def test_pending_resources_ignora_lo_que_esta_en_silver_y_no_en_bronze():
    assert pending_resources({"a": "sha-a"}, {"a": "sha-a", "viejo": "sha-x"}) == []


# --- umbral de rechazos y tablas derivadas ---------------------------------------------


def test_el_umbral_tolera_unas_pocas_filas():
    assert too_many_rejects(1_000_000, 12) is False


def test_el_umbral_corta_arriba_del_uno_por_ciento():
    assert too_many_rejects(1_000, 11) is True


def test_el_umbral_no_divide_por_cero():
    assert too_many_rejects(0, 0) is False


def test_tablas_derivadas_del_contrato():
    contract = contrato(columna("idpozo", "bigint"))
    assert rejects_table(contract) == "lake.silver.prueba_rejects"
    assert dq_runs_table(contract) == "lake.silver.dq_runs"


# --- checks duros sobre las mediciones --------------------------------------------------


def medidas(rows: int, keys: int, **nulls: int) -> Measures:
    return Measures(rows=rows, keys=keys, nulls=nulls)


def test_sin_problemas_no_hay_fallas_duras():
    assert hard_failures(medidas(1000, 1000, empresa=0, idpozo=0), 1010, 10) == []


def test_un_nulo_en_columna_obligatoria_es_falla_dura():
    fallas = hard_failures(medidas(1000, 1000, empresa=3), 1000, 0)
    assert fallas == ["empresa: 3 nulos en una columna no nullable"]


def test_claves_duplicadas_despues_de_deduplicar_es_falla_dura():
    fallas = hard_failures(medidas(1000, 998), 1000, 0)
    assert "clave primaria duplicada" in fallas[0]


def test_demasiados_rechazos_es_falla_dura():
    fallas = hard_failures(medidas(900, 900), 1000, 100)
    assert fallas == ["rechazos 10.00% sobre el umbral tolerado"]


def test_run_status_ok_y_failed():
    assert run_status(RunReport(resource_id="a")) == "ok"
    assert run_status(RunReport(resource_id="a", hard_failures=["algo"])) == "failed"
