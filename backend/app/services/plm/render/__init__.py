"""Renderers for structured PLM documents: PDF (ReportLab), Word (python-docx / docxtpl), labels."""


from .pdfa import PdfaError  # noqa: F401 - re-exported for API error mapping


class MissingRenderDependency(RuntimeError):
    """A render backend (reportlab / python-docx) is not installed.

    The render modules guard their third-party imports so a minimal install
    can still import the PLM service — only the actual render call fails,
    and API endpoints translate this to HTTP 503 (not 500: the smoke test
    allows 503, and a missing optional dependency is not a server bug).
    """

    def __init__(self, package: str) -> None:
        super().__init__(f"未安装 {package}（pip install {package}），无法渲染文档")
        self.package = package
