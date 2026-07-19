"""Mutable runtime state shared across modules.
Always access as state.user_email (module attribute), never `from state import
user_email` — importing the value would freeze it at import time."""

user_email = ""            # set after login
