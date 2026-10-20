"""The secure core as its own program — what the desktop wrapper starts and
talks to over stdin/stdout. Packaged per platform (PyInstaller) as the
`opal-service` sidecar; see desktop/README.md."""
import os
import sys

from inkguard import opal


def main() -> int:
    service = None
    dev_anchor = os.environ.get("DEID_DEV_ANCHOR_DIR", "")
    if dev_anchor and not getattr(sys, "frozen", False):
        # Development from source only (a packaged program ignores this):
        # stand in for the operating system's protected store on machines
        # without one, such as a Linux build box.
        try:
            service = opal.Service(anchor=opal.FolderAnchor(dev_anchor))
        except opal.ServiceError as e:
            sys.stdout.write('{"id": null, "ok": false, "error": {"code": "%s", "message": "%s"}}\n'
                             % (e.code, opal.topaz.message(e.code).replace('"', "'")))
            return 1
    try:
        return opal.serve(service=service)
    finally:
        if service is not None:
            service.close()


if __name__ == "__main__":
    sys.exit(main())
