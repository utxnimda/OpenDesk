"""Independent local YAML people/character records shared by feature adapters."""

from .model import validate_people_refs
from .repository import PeopleRepository

__all__ = ["PeopleRepository", "validate_people_refs"]
