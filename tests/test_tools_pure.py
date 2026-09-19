"""外部依存なしの Tool 単体テスト (MagicByte / CSVSniffer / TypeInfer / NameProposer)。

これらの Tool は S3 や Trino に触らず、入力から純関数的に結果を返す。
Cloudera AI Workbench 上でも同じ結果が出ることを担保する回帰テスト。
"""
from __future__ import annotations

import base64
from datetime import date, datetime

import pytest

from thor.tools.format import CSVSnifferTool, MagicByteTool
from thor.tools.schema import NameProposerTool, TypeInferTool, _infer_type_for_values


# ------------------------------------------------------------------ #
# MagicByteTool
# ------------------------------------------------------------------ #

def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


class TestMagicByteTool:
    def setup_method(self) -> None:
        self.tool = MagicByteTool()

    def test_xlsx_zip_magic(self) -> None:
        result = self.tool.run(user_ctx=None, content_b64=_b64(b"PK\x03\x04rest"))
        assert result["status"] == "ok"
        assert result["format"] == "xlsx"
        assert result["confidence"] == "high"

    def test_xls_ole2_magic(self) -> None:
        result = self.tool.run(
            user_ctx=None, content_b64=_b64(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1")
        )
        assert result["format"] == "xls"

    def test_parquet_magic(self) -> None:
        result = self.tool.run(user_ctx=None, content_b64=_b64(b"PAR1\x00\x00"))
        assert result["format"] == "parquet"

    def test_gzip_magic(self) -> None:
        result = self.tool.run(user_ctx=None, content_b64=_b64(b"\x1f\x8b\x08"))
        assert result["format"] == "gzip"

    def test_pdf_magic(self) -> None:
        result = self.tool.run(user_ctx=None, content_b64=_b64(b"%PDF-1.7"))
        assert result["format"] == "pdf"

    def test_json_leading_char(self) -> None:
        result = self.tool.run(
            user_ctx=None, content_b64=_b64(b'{"foo": "bar"}\n')
        )
        assert result["format"] == "json"

    def test_json_array_leading(self) -> None:
        result = self.tool.run(user_ctx=None, content_b64=_b64(b"[1, 2, 3]"))
        assert result["format"] == "json"

    def test_csv_uniform_delimiter(self) -> None:
        csv_bytes = b"a,b,c\n1,2,3\n4,5,6\n7,8,9\n"
        result = self.tool.run(user_ctx=None, content_b64=_b64(csv_bytes))
        assert result["format"] == "csv"
        assert result["delimiter_hint"] == ","

    def test_tsv_uniform_delimiter(self) -> None:
        tsv_bytes = b"a\tb\tc\n1\t2\t3\n4\t5\t6\n"
        result = self.tool.run(user_ctx=None, content_b64=_b64(tsv_bytes))
        assert result["format"] == "tsv"

    def test_utf8_bom_stripped_then_json(self) -> None:
        result = self.tool.run(
            user_ctx=None, content_b64=_b64(b"\xef\xbb\xbf{\"a\": 1}")
        )
        assert result["format"] == "json"

    def test_bad_base64(self) -> None:
        result = self.tool.run(user_ctx=None, content_b64="!!! not-base64 !!!")
        assert result["status"] == "error"
        assert result["error_code"] == "FORMAT_CORRUPT"

    def test_default_text(self) -> None:
        result = self.tool.run(
            user_ctx=None, content_b64=_b64(b"just some plain text without structure")
        )
        assert result["format"] == "text"


# ------------------------------------------------------------------ #
# CSVSnifferTool
# ------------------------------------------------------------------ #

class TestCSVSnifferTool:
    def setup_method(self) -> None:
        self.tool = CSVSnifferTool()

    def test_utf8_comma(self) -> None:
        csv = "name,age,city\nAlice,30,Tokyo\nBob,25,Osaka\n"
        result = self.tool.run(user_ctx=None, content_b64=_b64(csv.encode("utf-8")))
        assert result["status"] == "ok"
        assert result["encoding"] in ("utf-8", "utf-8-sig")
        assert result["delimiter"] == ","
        assert result["has_header"] is True
        assert result["preview_row_count"] == 3

    def test_utf8_bom_prefixed(self) -> None:
        csv = "﻿name,age\nAlice,30\n"
        result = self.tool.run(user_ctx=None, content_b64=_b64(csv.encode("utf-8")))
        assert result["status"] == "ok"
        assert result["encoding"] == "utf-8-sig"

    def test_semicolon_delimiter(self) -> None:
        csv = "name;age;city\nAlice;30;Tokyo\nBob;25;Osaka\n"
        result = self.tool.run(user_ctx=None, content_b64=_b64(csv.encode("utf-8")))
        assert result["status"] == "ok"
        assert result["delimiter"] == ";"

    def test_tab_delimiter(self) -> None:
        csv = "name\tage\tcity\nAlice\t30\tTokyo\nBob\t25\tOsaka\n"
        result = self.tool.run(user_ctx=None, content_b64=_b64(csv.encode("utf-8")))
        assert result["status"] == "ok"
        assert result["delimiter"] == "\t"

    def test_bad_base64(self) -> None:
        result = self.tool.run(user_ctx=None, content_b64="!!!bad!!!")
        assert result["status"] == "error"
        assert result["error_code"] == "FORMAT_CORRUPT"


# ------------------------------------------------------------------ #
# TypeInferTool
# ------------------------------------------------------------------ #

class TestTypeInferTool:
    def setup_method(self) -> None:
        self.tool = TypeInferTool()

    def test_boolean(self) -> None:
        result = self.tool.run(user_ctx=None, columns={"flag": [True, False, True]})
        assert result["status"] == "ok"
        col = result["columns"][0]
        assert col["trino_type"] == "BOOLEAN"
        assert col["nullable"] is False

    def test_bigint_from_ints(self) -> None:
        result = self.tool.run(user_ctx=None, columns={"n": [1, 2, 3, 100]})
        assert result["columns"][0]["trino_type"] == "BIGINT"

    def test_double_from_floats(self) -> None:
        result = self.tool.run(user_ctx=None, columns={"x": [1.5, 2.75, 3.0]})
        assert result["columns"][0]["trino_type"] == "DOUBLE"

    def test_date_python_objects(self) -> None:
        result = self.tool.run(
            user_ctx=None,
            columns={"d": [date(2024, 1, 1), date(2024, 12, 31)]},
        )
        assert result["columns"][0]["trino_type"] == "DATE"

    def test_timestamp_python_objects(self) -> None:
        result = self.tool.run(
            user_ctx=None,
            columns={"ts": [datetime(2024, 1, 1, 12, 0), datetime(2024, 12, 31, 23, 59)]},
        )
        assert result["columns"][0]["trino_type"] == "TIMESTAMP(6)"

    def test_bigint_from_int_strings(self) -> None:
        result = self.tool.run(
            user_ctx=None, columns={"n": ["1", "-42", "100", "0"]}
        )
        assert result["columns"][0]["trino_type"] == "BIGINT"

    def test_decimal_from_strings(self) -> None:
        result = self.tool.run(
            user_ctx=None, columns={"amt": ["1.50", "2.75", "100.00"]}
        )
        t = result["columns"][0]["trino_type"]
        assert t.startswith("DECIMAL(")
        assert t.endswith(",2)")

    def test_date_parsed_from_iso_strings(self) -> None:
        result = self.tool.run(
            user_ctx=None, columns={"d": ["2024-01-15", "2024-06-30"]}
        )
        assert result["columns"][0]["trino_type"] == "DATE"

    def test_timestamp_parsed_from_strings(self) -> None:
        result = self.tool.run(
            user_ctx=None,
            columns={"ts": ["2024-01-15 12:30:00", "2024-06-30 18:45:00"]},
        )
        assert result["columns"][0]["trino_type"] == "TIMESTAMP(6)"

    def test_varchar_with_length(self) -> None:
        result = self.tool.run(
            user_ctx=None, columns={"name": ["Alice", "Bob", "Charlie"]}
        )
        t = result["columns"][0]["trino_type"]
        assert t.startswith("VARCHAR(")

    def test_all_nulls_fallback(self) -> None:
        result = self.tool.run(user_ctx=None, columns={"empty": [None, None, ""]})
        col = result["columns"][0]
        assert col["trino_type"] == "VARCHAR"
        assert col["nullable"] is True
        assert col["sample_size"] == 0

    def test_nullable_detected(self) -> None:
        result = self.tool.run(user_ctx=None, columns={"n": [1, None, 3]})
        col = result["columns"][0]
        assert col["nullable"] is True
        assert col["sample_size"] == 2

    def test_mixed_falls_back_to_varchar(self) -> None:
        result = self.tool.run(
            user_ctx=None, columns={"mixed": ["abc", "123", "2024-01-01"]}
        )
        t = result["columns"][0]["trino_type"]
        assert t.startswith("VARCHAR")

    def test_infer_helper_directly(self) -> None:
        assert _infer_type_for_values([1, 2, 3], 4000) == "BIGINT"
        assert _infer_type_for_values(["true", "false"], 4000) == "BOOLEAN"


# ------------------------------------------------------------------ #
# NameProposerTool
# ------------------------------------------------------------------ #

class TestNameProposerTool:
    def setup_method(self) -> None:
        self.tool = NameProposerTool()

    def test_simple_filename(self) -> None:
        result = self.tool.run(user_ctx=None, source_hint="sales_2024.xlsx")
        assert result["status"] == "ok"
        assert result["proposed_table_name"] == "sales_2024"
        assert result["fq_name"] == "iceberg.default.sales_2024"

    def test_with_schema_prefix(self) -> None:
        result = self.tool.run(
            user_ctx=None, source_hint="customers.csv", schema_prefix="demo"
        )
        assert result["proposed_table_name"] == "customers"
        assert result["fq_name"] == "iceberg.demo.customers"

    def test_s3_key_stripped(self) -> None:
        result = self.tool.run(
            user_ctx=None,
            source_hint="raw/2024/09/orders_data.parquet",
            schema_prefix="raw",
        )
        assert result["proposed_table_name"] == "orders_data"

    def test_non_ascii_chars_replaced(self) -> None:
        result = self.tool.run(user_ctx=None, source_hint="Sales Data (2024).xlsx")
        assert result["proposed_table_name"] == "sales_data_2024"

    def test_leading_digit_prefixed(self) -> None:
        result = self.tool.run(user_ctx=None, source_hint="2024_sales.csv")
        assert result["proposed_table_name"].startswith("t_")

    def test_long_name_truncated(self) -> None:
        long_name = "a" * 100 + ".csv"
        result = self.tool.run(user_ctx=None, source_hint=long_name)
        assert len(result["proposed_table_name"]) <= 63

    def test_custom_catalog(self) -> None:
        result = self.tool.run(
            user_ctx=None, source_hint="data.csv", catalog="hive"
        )
        assert result["fq_name"].startswith("hive.")

    def test_japanese_filename_sanitized(self) -> None:
        # 日本語などの非 ASCII は _ に置換され、残った ASCII のみ生き残る
        result = self.tool.run(user_ctx=None, source_hint="売上_2024.xlsx")
        assert result["status"] == "ok"
        assert result["proposed_table_name"]
