"""Control-only client of the independent local output service."""
import socket

from transport import TransportError
from transport_service import read_message, write_message


class Client:
    def __init__(self, path, channel):
        self.path, self.channel = str(path), channel

    def _request(self, operation, config=None, commit=None):
        def receive(stream):
            try:
                response = read_message(stream)
            except ValueError:
                raise OSError("Risposta del distributore interrotta o non valida.") from None
            if "error" in response:
                error = response["error"]
                raise TransportError(error["field"], error["message"])
            if response.get("ready") is not True and not isinstance(response.get("state"), dict):
                raise OSError("Risposta del distributore non valida.")
            return response
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(1 if operation == "state" else 8)
            connection.connect(self.path)
            with connection.makefile("rwb") as stream:
                write_message(stream, {"version": 1, "op": operation, "channel": self.channel, **(config or {})})
                response = receive(stream)
                if response.get("ready"):
                    if commit:
                        commit()
                    write_message(stream, {"commit": True})
                    response = receive(stream)
                if "state" not in response:
                    raise OSError("Conferma del distributore non ricevuta.")
                return response["state"]

    def start(self, outputs, credentials=None, primary_url=None):
        return self.reconfigure(outputs, credentials, primary_url)["input_url"]

    def reconfigure(self, outputs, credentials=None, primary_url=None, commit=None):
        return self._request("configure", {"outputs": outputs, "credentials": credentials or {},
                                          "primary_url": primary_url}, commit)

    def state(self):
        try:
            return self._request("state")
        except (OSError, ValueError, KeyError):
            return {"running": False, "outputs": [], "input_status": "unknown",
                    "independent_process": True, "service_available": False,
                    "last_error": "Distributore non disponibile; verifica il servizio delle uscite."}

    def remove(self):
        return self._request("remove")
