"""CrewAI Tool 実装群 (S3 / Trino / Format 判定 / スキーマ推定)。

公開 Tool は :class:`thor.transport.tool_base.BaseThorTool` を継承し、
``requires_auth=True`` のものは Knox JWT / STS 資格情報が必須。

再エクスポート:
  S3:      :class:`S3ListTool`, :class:`S3HeadTool`, :class:`S3GetRangeTool`
  Trino:   :class:`TrinoQueryTool`, :class:`TrinoDDLTool`, :class:`TrinoMetaTool`
  Format:  :class:`MagicByteTool`, :class:`CSVSnifferTool`, :class:`ParquetMetaTool`
  Excel:   :class:`ExcelHeaderDetectTool`
  Schema:  :class:`TypeInferTool`, :class:`NameProposerTool`
"""
from thor.tools.excel import ExcelHeaderDetectTool
from thor.tools.format import CSVSnifferTool, MagicByteTool, ParquetMetaTool
from thor.tools.s3 import S3GetRangeTool, S3HeadTool, S3ListTool
from thor.tools.schema import NameProposerTool, TypeInferTool
from thor.tools.trino import TrinoDDLTool, TrinoMetaTool, TrinoQueryTool

__all__ = [
    # s3
    "S3ListTool",
    "S3HeadTool",
    "S3GetRangeTool",
    # trino
    "TrinoQueryTool",
    "TrinoDDLTool",
    "TrinoMetaTool",
    # format
    "MagicByteTool",
    "CSVSnifferTool",
    "ParquetMetaTool",
    # excel
    "ExcelHeaderDetectTool",
    # schema
    "TypeInferTool",
    "NameProposerTool",
]
