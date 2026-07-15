class BudgetError(RuntimeError):
    pass


class BudgetInsufficientError(BudgetError):
    """Il saldo crediti non copre il costo stimato dell'operazione/piano."""

    def __init__(self, needed: float, available: float):
        self.needed = needed
        self.available = available
        super().__init__(
            f"Saldo insufficiente: servono {needed:.2f} crediti, disponibili {available:.2f}."
        )
