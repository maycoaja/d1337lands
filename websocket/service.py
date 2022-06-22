from functools import lru_cache
import eventlet
import socketio
import requests
import random
import json

from gql import Client, gql
from gql.transport.requests import RequestsHTTPTransport

from subservices.chatService import ChatNamespace

game_url = "http://localhost:8080"
auth_validation_url = "http://localhost:4444/api/authentication/validate"
users_presence_url = "http://localhost:3000/api/user/presence"
graphql_endpoint_url = "http://localhost:3333/v1/graphql"
hasura_admin_secret = "hSK6kPeZN2zTLsvd2grPNtapLbeNzD9QU9aPd38f894JsmxM7Ecpb9hkAxeX"


def getMapData(map_name):
    map_metadata = {}
    with requests.get(f"{game_url}/assets/maps/{map_name}/positioning.json") as r:
        map_metadata = json.loads(r.text)

    map_size = {}
    with requests.get(f"{game_url}/assets/maps/{map_name}/{map_name}.json") as r:
        data = json.loads(r.text)
        map_size = {"width": int(data["width"]), "height": int(data["height"])}

    map_data = {
        "collisions": map_metadata["Collision"],
        "map_size": map_size,
    } 

    if map_name == "town":
        map_data["start_positions"] = map_metadata["RandomStart"]

        map_data["events"] = {
            # Main events
            "shop": map_metadata["ShopPos"],
            "leaderboard": map_metadata["LeaderboardPos"],
            "hall_of_fame": map_metadata["HoFPos"],
            "luck_pond": map_metadata["LuckPondPos"],
            "teleportation": map_metadata["TeleportationPos"],

            # Mentor castle teleportation
            "mentor_castle_right": map_metadata["MentorCastleRightPos"],
            "mentor_castle_left": map_metadata["MentorCastleLeftPos"],

            # Easter egg stuff
            "easter_egg_random": map_metadata["EasterEggRandomStart"],
            "easter_egg_activation": map_metadata["EasterEggActivationPos"],

            # Secret chess stuff
            "secret_chess_level_1": map_metadata["SecretChess1Pos"],
            "secret_chess_level_2": map_metadata["SecretChess2Pos"],
            "secret_chess_level_1_activation": map_metadata["SecretChess1ActivationPos"],
            "secret_chess_level_2_activation": map_metadata["SecretChess2ActivationPos"],
        }
    elif map_name == "mentorcastle":
        map_data["start_positions"] = [map_metadata["TeleportationLeftPos"], map_metadata["TeleportationRightPos"]]

        map_data["events"] = {
            # Main events
            "submission_check": map_metadata["SubmissionCheckPos"],
        }
    else:
        map_data["start_positions"] = map_metadata["TeleportationPos"]

        map_data["events"] = {
            # Main events
            "hint": map_metadata["HintPos"],
            "quest": map_metadata["QuestPos"],
            "submission": map_metadata["SubmissionPos"],
            "submit_quest": map_metadata["SubmitQuestPos"],
        }

    return map_data


maps = [
    "town",
    "codeisland",
    "algoisland",
    "hackisland",
    "dataisland",
    "netisland",
    "mentorcastle",
]

maps_data = {}
for map in maps:
    maps_data[map] = getMapData(map)

sio = socketio.Server(
    cors_allowed_origins=[
        "http://localhost:4444",
        "http://localhost:7777",
        "http://localhost:3000",
        "http://localhost:8080",
    ]
)
app = socketio.WSGIApp(sio)


def getRandomStartPosition(map_name):
    return random.choice(maps_data[map_name]["start_positions"])


@lru_cache
def getNextPosition(map_name, position, direction):
    """
    Check if character can move to next position or not
    and get the next position if they can

    Note:
        * position should be 1-indexed number for the
        specific calculation implemented here
        * collision here will be considered as boolean
        value with either 0 (false) or more than 0 (true)
    """
    position = int(position)

    next_position = None
    if direction == "up" and position > maps_data[map_name]["map_size"]["width"]:
        next_position = position - maps_data[map_name]["map_size"]["width"]
    if direction == "left" and position % maps_data[map_name]["map_size"]["width"] != 1:
        next_position = position - 1
    if direction == "down" and position < (
        maps_data[map_name]["map_size"]["width"]
        * maps_data[map_name]["map_size"]["height"]
    ) - (maps_data[map_name]["map_size"]["width"] - 1):
        next_position = position + maps_data[map_name]["map_size"]["width"]
    if (
        direction == "right"
        and position % maps_data[map_name]["map_size"]["width"] != 0
    ):
        next_position = position + 1

    if next_position and next_position not in maps_data[map_name]["collisions"]:
        return next_position
    else:
        return position


def isFromWeb(connection_source):
    return connection_source == "web"
    

@sio.event
def connect(sid, _, auth):
    req = requests.post(
        auth_validation_url, headers={"Authorization": f"Bearer {auth['token']}"}
    )
    if req.status_code == 401:
        return "ERR", 401

    if req.status_code != 200:
        return "ERR", req.status_code

    user_data = json.loads(req.text)
    user_id = user_data["id"]
    user_name = user_data["name"]
    user_role = user_data["role"]
    user_nickname = user_data["nickname"]

    user_session = {
        "user_id": user_id,
        "user_name": user_name,
        "user_role": user_role,
        "user_nickname": user_nickname,
        "connection_source": auth["connection_source"],
    }

    sio.save_session(sid, user_session)

    transport = RequestsHTTPTransport(
        url=graphql_endpoint_url,
        verify=True,
        retries=3,
        headers={
            "content-type": "application/json",
            "Authorization": "Bearer {}".format(auth["token"]),
        },
    )
    client = Client(transport=transport, fetch_schema_from_transport=True)

    try:
        query = gql(
            r"""
            query getUserData ($userId: bigint!) {
                user_datas(where: {user_id: {_eq: $userId}}) {
                    map
                    position
                }
            }
        """
        )

        result = client.execute(query, variable_values={"userId": user_id})

        if len(result["user_datas"]) != 0:
            user_session["user_datas"] = result["user_datas"][0]
            sio.save_session(sid, user_session)
    except:
        pass

    sio.enter_room(sid, user_id)
    print(f"User connected to game socket: {sid}\n\n")

    if isFromWeb(auth["connection_source"]):
        _ = client.execute(
            gql(
                r"""
                mutation updateUserData($userId: bigint!, $is_online: Boolean!) {
                    update_user_datas(where: {user_id: {_eq: $userId}}, _set: {is_online: $is_online}) {
                        affected_rows
                    }
                }
            """
            ),
            variable_values={
                "userId": user_id,
                "is_online": True,
            },
        )

        sio.emit(
            "user_connect",
            {
                "user_id": user_id,
                "user_nickname": user_nickname,
                "user_role": user_role,
                "user_datas": result["user_datas"][0]
                if len(result["user_datas"]) > 0
                else {},
            },
        )

        with requests.get(
            users_presence_url, headers={"Authorization": f"Bearer {auth['token']}"}
        ) as r:
            for user in json.loads(r.text)["result"]:
                if user["is_online"] and user["user_id"] != user_id:
                    sio.emit(
                        "user_connect",
                        {
                            "user_id": user["user_id"],
                            "user_nickname": user["nickname"],
                            "user_role": user["role"],
                            "user_datas": {
                                "map": user["map"],
                                "position": user["position"],
                            },
                        },
                        room=user_id,
                    )

    sio.emit(
        "user_data",
        {
            "user_id": user_id,
            "user_name": user_name,
            "user_role": user_role,
            "user_nickname": user_nickname,
            "user_datas": result["user_datas"][0]
            if len(result["user_datas"]) > 0
            else {},
        },
        room=user_id,
    )

    return "OK", 200


@sio.event
def disconnect(sid):
    session = sio.get_session(sid)

    if isFromWeb(session["connection_source"]):
        transport = RequestsHTTPTransport(
            url=graphql_endpoint_url,
            verify=True,
            retries=3,
            headers={
                "content-type": "application/json",
                "x-hasura-admin-secret": hasura_admin_secret,
            },
        )
        client = Client(transport=transport, fetch_schema_from_transport=True)

        _ = client.execute(
            gql(
                r"""
                mutation updateUserData($userId: bigint!, $map: String!, $position: String!, $is_online: Boolean!) {
                    update_user_datas(where: {user_id: {_eq: $userId}}, _set: {map: $map, position: $position, is_online: $is_online}) {
                        affected_rows
                    }
                }
            """
            ),
            variable_values={
                "userId": session["user_id"],
                "map": session["user_datas"]["map"],
                "position": f"{session['user_datas']['position']}",
                "is_online": False,
            },
        )

    data_to_emit = {
        "map": {
            "user_id": session["user_id"],
            "user_nickname": session["user_nickname"],
            "map": session["user_datas"]["map"],
            "position": "-1",
        }
    }

    sio.emit(
        "user_disconnect",
        {
            "user_id": session["user_id"],
            "user_nickname": session["user_nickname"],
            "user_role": session["user_role"],
        },
    )

    sio.emit(
        "map_state",
        data_to_emit["map"],
        room=data_to_emit["map"]["map"],
        skip_sid=sid,
    )

    sio.leave_room(sid, session["user_id"])
    print(f"User disconnected from game socket: {sid}\n\n")
    return "OK", 200


@sio.event
def send_action(sid, data):
    session = sio.get_session(sid)

    data_to_emit = {}
    if data["action"] == "initialize_data":
        initial_user_datas = {
            "map": "town",
            "position": f"{getRandomStartPosition('town')}",
        }

        try:
            transport = RequestsHTTPTransport(
                url=graphql_endpoint_url,
                verify=True,
                retries=3,
                headers={
                    "content-type": "application/json",
                    "x-hasura-admin-secret": hasura_admin_secret,
                },
            )
            client = Client(transport=transport, fetch_schema_from_transport=True)

            query = gql(
                r"""
                mutation insertUserData($userId: bigint!, $map: String!, $position: String!) {
                    insert_user_datas_one(object: {map: $map, position: $position, user_id: $userId}) {
                        id
                    }
                }
            """
            )

            _ = client.execute(
                query,
                variable_values={"userId": session["user_id"], **initial_user_datas},
            )
        except:
            return "ERR", 500

        data_to_emit["action"] = {"action": data["action"], **initial_user_datas}

        data_to_emit["map"] = {
            "user_id": session["user_id"],
            "user_nickname": session["user_nickname"],
            **initial_user_datas,
        }

        session["user_datas"] = initial_user_datas
        sio.save_session(sid, session)

    elif data["action"] == "move":
        next_position = getNextPosition(
            session["user_datas"]["map"],
            session["user_datas"]["position"],
            data["direction"],
        )

        new_user_datas = {
            "map": session["user_datas"]["map"],
            "position": next_position,
        }

        data_to_emit["action"] = {
            "action": data["action"],
            "direction": data["direction"],
            **new_user_datas,
        }

        data_to_emit["map"] = {
            "user_id": session["user_id"],
            "user_nickname": session["user_nickname"],
            **new_user_datas,
        }

        session["user_datas"] = new_user_datas
        sio.save_session(sid, session)

    if data_to_emit["action"]:
        sio.emit(
            "handle_action",
            data_to_emit["action"],
            room=session["user_id"],
            skip_sid=sid,
        )

    if data_to_emit["map"]:
        sio.emit(
            "map_state",
            data_to_emit["map"],
            room=data_to_emit["map"]["map"],
            skip_sid=sid,
        )

    return "OK", 200


@sio.event
def enter_map(sid, data):
    sio.enter_room(sid, data["map"])
    return "OK", 200


@sio.event
def leave_map(sid, data):
    sio.leave_room(sid, data["map"])
    return "OK", 200


sio.register_namespace(ChatNamespace("/chat"))


if __name__ == "__main__":
    eventlet.wsgi.server(eventlet.listen(("", 5000)), app)
