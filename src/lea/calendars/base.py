"""Interface commune des agendas."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from datetime import datetime


@dataclass
class RendezVous:
    nom: str
    telephone: str
    motif: str
    debut: datetime
    fin: datetime
    depuis_quand: str | None = None
    niveau_douleur: int | None = None
    urgence: bool = False
    id: str | None = None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["debut"] = self.debut.isoformat()
        d["fin"] = self.fin.isoformat()
        return d

    @classmethod
    def from_dict(cls, d: dict) -> RendezVous:
        d = dict(d)
        d["debut"] = datetime.fromisoformat(d["debut"])
        d["fin"] = datetime.fromisoformat(d["fin"])
        return cls(**d)

    def description(self) -> str:
        """Texte de description (utilisé notamment par Google Calendar)."""
        lignes = [
            f"Motif : {self.motif}",
            f"Patient : {self.nom}",
            f"Téléphone : {self.telephone}",
        ]
        if self.depuis_quand:
            lignes.append(f"Depuis : {self.depuis_quand}")
        if self.niveau_douleur is not None:
            lignes.append(f"Douleur : {self.niveau_douleur}/10")
        if self.urgence:
            lignes.append("⚠️ URGENCE signalée pendant l'appel")
        lignes.append("Pris par Léa (réceptionniste IA)")
        return "\n".join(lignes)


class Calendar(ABC):
    """Un agenda sait lister les plages occupées et créer un rendez-vous."""

    @abstractmethod
    def busy_intervals(self, start: datetime, end: datetime) -> list[tuple[datetime, datetime]]:
        """Plages occupées qui chevauchent [start, end[ (datetimes avec fuseau)."""

    @abstractmethod
    def create_appointment(self, rdv: RendezVous) -> RendezVous:
        """Enregistre le rendez-vous et le renvoie avec son identifiant."""

    def list_appointments(
        self, start: datetime | None = None, end: datetime | None = None
    ) -> list[RendezVous]:
        """Rendez-vous (pris par Léa) entre start et end, triés par date."""
        raise NotImplementedError
