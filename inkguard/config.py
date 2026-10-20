"""
Settings that decide what the tool removes and how it behaves on failure, in
one place so they can be read and checked together. They are fixed: no
environment variable weakens redaction, so what the audit trail records is
what ran.
"""
import os


class Settings:
    def __init__(self) -> None:
        # A name-recogniser score below this is "unsure" rather than
        # "confirmed". Unsure hits are still removed or sent to a person.
        self.confidence_threshold = 0.4

        # If the scanner fails on a note, the note is held, never passed
        # through as it was. Fixed on.
        self.fail_closed = True

        # The detector types the engine looks for. They are chosen with
        # HIPAA's Safe Harbor categories in mind (names, dates, record and
        # plan numbers, contact details, device and other identifiers), but
        # this is not a one-to-one list of the Safe Harbor identifiers: some
        # entries are detector labels, ZIP codes and ages over 89 are handled
        # in hipaa.py, and some categories (photographs, biometrics) are
        # outside what a text tool can find.
        self.phi_entities = [
            "PERSON", "PHONE_NUMBER", "EMAIL_ADDRESS", "LOCATION",
            "DATE_TIME", "US_SSN", "MEDICAL_LICENSE", "URL", "IP_ADDRESS",
            "US_DRIVER_LICENSE", "CREDIT_CARD", "US_BANK_NUMBER",
            "MRN", "HEALTH_PLAN_ID", "NPI", "DEVICE_ID",
            "DATE_MONTHNAME", "PHONE_INTL",
        ]

        # Types that may be KEPT. Empty: nothing is kept. Only non-Safe-Harbor
        # types (for example FACILITY) could ever be kept; names, dates, IDs
        # and the other Safe Harbor identifiers are removed whatever is listed.
        self.keep_entities: list[str] = []

        # Safe Harbor removes places smaller than a state; the state itself
        # may stay. States are removed anyway, because a state combined with
        # other details narrows a group more than people expect.
        self.redact_state_names = True

        # "mask" writes the type in brackets ([NAME]); "pseudonym" writes a
        # code from the keyed code table. The older "hash" mode is refused
        # unless an Expert Determination is recorded (PHI_ALLOW_DERIVED_CODES).
        self.redaction_mode = os.getenv("REDACTION_MODE", "mask")


settings = Settings()
