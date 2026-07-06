import inspect
import re

from ibkr_control.api import setup


def test_step3_uses_hardened_account_helper_not_inline_create():
    """HD-5b: step3 no debe recrear el write-path de cuentas inline (racy). Debe
    delegar en _ensure_accounts (ON CONFLICT + re-select). Guard de source: la 3ra
    copia era `if acc is None: session.add(Account(...))`.
    """
    # localizar el handler step3 (save_new_accounts) por nombre
    fn = getattr(setup, "step3_save_new_accounts", None) or getattr(
        setup, "save_new_accounts", None
    )
    assert fn is not None, "no encontré el handler de step3 — ajustá el nombre"
    src = inspect.getsource(fn)
    assert "_ensure_accounts" in src, "step3 no usa el helper endurecido _ensure_accounts"
    # Word-boundary: matchea el constructor `Account(` (racy) pero NO `select(Account)`
    # (tras `Account` viene `)`) ni un futuro `FlexImportAccount(` (sin word boundary
    # antes de `Account`) — evita el falso positivo que el review holístico señaló.
    assert re.search(r"\bAccount\(", src) is None, (
        "step3 aún crea Account inline (write-path racy — HD-5b)"
    )
