from abc import ABC, abstractmethod


def is_user_facing(error):
    """Whether `error`'s own message is the whole explanation.

    Raised deliberately once a run has decided it cannot continue — a spent
    plan quota, an endpoint that cannot size a context window — so a loader
    prints it and fails, rather than adding a traceback to a message that
    needs none, or treating it like an interrupt and reporting success.
    """
    return getattr(error, "user_facing", False)


class BaseBookLoader(ABC):
    @staticmethod
    def _is_special_text(text):
        return text.isdigit() or text.isspace()

    @abstractmethod
    def _make_new_book(self, book):
        pass

    @abstractmethod
    def make_bilingual_book(self):
        pass

    @abstractmethod
    def load_state(self):
        pass

    @abstractmethod
    def _save_temp_book(self):
        pass

    @abstractmethod
    def _save_progress(self):
        pass
