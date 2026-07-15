"""Quote per giorno / per tipo di contenuto in un PlanWeek.

I limiti NON sono fissati nel blueprint (dice solo "gestisce quote per
giorno/tipo"): la policy e' quindi parametrica, con default permissivo
(nessun limite) finche' l'utente non fornisce i numeri reali. Quando li
dara', si passa una QuotaPolicy popolata a `add_content_piece`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

GIORNI_VALIDI = ("lun", "mar", "mer", "gio", "ven", "sab", "dom")


class QuotaExceededError(RuntimeError):
    pass


@dataclass
class QuotaPolicy:
    # Massimo numero di pezzi schedulabili nello stesso giorno (qualsiasi tipo).
    max_pezzi_per_giorno: Optional[int] = None
    # Massimo numero di pezzi per content_type in tutta la settimana.
    max_per_tipo_settimana: dict = field(default_factory=dict)

    def check(self, *, content_type: str, scheduled_day: Optional[str], pezzi_esistenti: list) -> None:
        """Solleva QuotaExceededError se aggiungere questo pezzo viola la policy.

        `pezzi_esistenti` = lista di (content_type, scheduled_day) gia' nel piano.
        """
        if self.max_pezzi_per_giorno is not None and scheduled_day is not None:
            nel_giorno = sum(1 for _, day in pezzi_esistenti if day == scheduled_day)
            if nel_giorno + 1 > self.max_pezzi_per_giorno:
                raise QuotaExceededError(
                    f"Quota giornaliera superata per '{scheduled_day}': "
                    f"max {self.max_pezzi_per_giorno}, gia' presenti {nel_giorno}."
                )

        limite_tipo = self.max_per_tipo_settimana.get(content_type)
        if limite_tipo is not None:
            del_tipo = sum(1 for ct, _ in pezzi_esistenti if ct == content_type)
            if del_tipo + 1 > limite_tipo:
                raise QuotaExceededError(
                    f"Quota settimanale superata per tipo '{content_type}': "
                    f"max {limite_tipo}, gia' presenti {del_tipo}."
                )
