import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import game_mover_minecraft as minecraft


class FakeSocket:
    def __init__(self, response):
        self.response = bytearray(response)
        self.sent = bytearray()

    def __enter__(self):
        return self

    def __exit__(self, _type, _value, _traceback):
        return False

    def settimeout(self, _timeout):
        pass

    def sendall(self, payload):
        self.sent.extend(payload)

    def recv(self, length):
        chunk = self.response[:length]
        del self.response[:length]
        return bytes(chunk)


class MinecraftStatusTest(unittest.TestCase):
    def test_local_server_addresses_include_loopback_fallbacks(self):
        addresses = minecraft.local_server_addresses()
        self.assertIn("127.0.0.1", addresses)
        self.assertIn("::1", addresses)

    def test_status_query_reads_online_and_max_players(self):
        payload = json.dumps({"players": {"online": 3, "max": 20}}).encode("utf-8")
        body = b"\x00" + minecraft._encode_varint(len(payload)) + payload
        response = minecraft._encode_varint(len(body)) + body
        fake_socket = FakeSocket(response)
        with patch.object(
            minecraft.socket, "create_connection", return_value=fake_socket,
        ) as create_connection:
            result = minecraft.query_server_status("127.0.0.1", 25570)
        self.assertEqual(result, {"online": 3, "max": 20})
        self.assertIn(minecraft._encode_varint(763), fake_socket.sent)
        create_connection.assert_called_once_with(("127.0.0.1", 25570), timeout=8.0)

    def test_valid_status_survives_missing_forge_pong(self):
        payload = json.dumps({"players": {"online": 2, "max": 12}}).encode("utf-8")
        body = b"\x00" + minecraft._encode_varint(len(payload)) + payload
        fake_socket = FakeSocket(minecraft._encode_varint(len(body)) + body)

        with patch.object(minecraft.socket, "create_connection", return_value=fake_socket):
            result = minecraft.query_server_status("192.0.2.66", 25565)

        self.assertEqual(result, {"online": 2, "max": 12})

    def test_rcon_list_reads_online_and_max_players(self):
        request_id = 0x474D
        response = (
            minecraft._rcon_packet(request_id, 2, "")
            + minecraft._rcon_packet(
                request_id, 0,
                "There are 2 of a max of 20 players online: Bernye, Alex",
            )
        )
        fake_socket = FakeSocket(response)
        with patch.object(
            minecraft.socket, "create_connection", return_value=fake_socket,
        ) as create_connection:
            result = minecraft.query_server_rcon(
                "127.0.0.1", 25575, "secret", timeout=3.0,
            )
        self.assertEqual(result, {"online": 2, "max": 20})
        self.assertIn(b"secret", fake_socket.sent)
        self.assertIn(b"list", fake_socket.sent)
        create_connection.assert_called_once_with(("127.0.0.1", 25575), timeout=3.0)

    def test_execute_rcon_command_returns_server_response(self):
        request_id = 0x474D
        fake_socket = FakeSocket(
            minecraft._rcon_packet(request_id, 2, "")
            + minecraft._rcon_packet(request_id, 0, "Made Bernye a server operator")
        )
        with patch.object(minecraft.socket, "create_connection", return_value=fake_socket):
            response = minecraft.execute_rcon_command(
                "127.0.0.1", 25575, "secret", "op Bernye",
            )
        self.assertEqual(response, "Made Bernye a server operator")
        self.assertIn(b"op Bernye", fake_socket.sent)

    def test_known_players_uses_configured_world_and_uuid_files(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            data = Path(temporary_directory)
            (data / "server.properties").write_text("level-name=my-world\n", encoding="utf-8")
            player_data = data / "my-world" / "playerdata"
            player_data.mkdir(parents=True)
            (player_data / "17aeaf09-24d4-47b4-a1dd-2aa945960095.dat").touch()
            (player_data / "not-a-player.dat").touch()
            self.assertEqual(minecraft.count_known_players(str(data)), 1)

    def test_known_players_rejects_world_outside_data_directory(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            data = Path(temporary_directory)
            (data / "server.properties").write_text("level-name=../other\n", encoding="utf-8")
            self.assertIsNone(minecraft.count_known_players(str(data)))

    def test_known_players_falls_back_to_user_cache(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            data = Path(temporary_directory)
            (data / "usercache.json").write_text(
                json.dumps([
                    {
                        "name": "Bernye",
                        "uuid": "17aeaf09-24d4-47b4-a1dd-2aa945960095",
                    }
                ]),
                encoding="utf-8",
            )
            self.assertEqual(minecraft.count_known_players(str(data)), 1)

    def test_known_players_deduplicates_world_and_user_cache(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            data = Path(temporary_directory)
            player_id = "17aeaf09-24d4-47b4-a1dd-2aa945960095"
            player_data = data / "world" / "playerdata"
            player_data.mkdir(parents=True)
            (player_data / f"{player_id}.dat").touch()
            (data / "usercache.json").write_text(
                json.dumps([{"name": "Bernye", "uuid": player_id}]),
                encoding="utf-8",
            )
            self.assertEqual(minecraft.count_known_players(str(data)), 1)

    def test_configured_server_port_reads_and_validates_properties(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            data = Path(temporary_directory)
            properties = data / "server.properties"
            properties.write_text("server-port=25565\n", encoding="utf-8")
            self.assertEqual(minecraft.configured_server_port(str(data)), 25565)
            properties.write_text("server-port=70000\n", encoding="utf-8")
            self.assertIsNone(minecraft.configured_server_port(str(data)))

    def test_configured_rcon_requires_enabled_valid_secret(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            data = Path(temporary_directory)
            properties = data / "server.properties"
            properties.write_text(
                "enable-rcon=true\nrcon.port=25575\nrcon.password=secret\n",
                encoding="utf-8",
            )
            self.assertEqual(
                minecraft.configured_rcon(str(data)),
                {"port": 25575, "password": "secret"},
            )
            properties.write_text(
                "enable-rcon=false\nrcon.password=secret\n", encoding="utf-8",
            )
            self.assertIsNone(minecraft.configured_rcon(str(data)))


if __name__ == "__main__":
    unittest.main()
