"""Named outreach errors. Each is raised at the boundary that finds the fault, before
any provider call or write, and never carries a Lead's personal data."""


class OutreachConfigError(ValueError):
    """An outreach configuration file cannot be turned into settings.

    Names the file and the offending key path, never the offending value.
    """

    def __init__(self, path: str, *, key_path: str, detail: str) -> None:
        self.path = path
        self.key_path = key_path
        self.detail = detail
        super().__init__(
            f"[{path}] OutreachConfigError: key={key_path or '<document>'}: {detail}"
        )
