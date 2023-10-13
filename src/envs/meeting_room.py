from typing import SupportsFloat, Any, Dict, Tuple
from enum import Enum
from queue import Queue

import pygame
import numpy as np
from numpy.random import randint
from gymnasium import Env, spaces
from gymnasium.core import RenderFrame, ActType, ObsType

from utils.render import draw_arrow

PLAYER_VIEW_SIZE = 7  # odd number

MARGIN = 50
FIELD_SIZE = 40
FLOOR_HEIGHT = 40
BUILDING_WIDTH = 70
BORDER_WIDTH = 2

PLAYER_COLOR = "#FF9911"
TARGET_COLOR = "#11FF99"
ENTRANCE_COLOR = "#11AAFF"
ELEVATOR_COLOR = ENTRANCE_COLOR


class Action(Enum):
    NORTH = 0
    EAST = 1
    SOUTH = 2
    WEST = 3
    NEXT = 4  # next level or building
    PREV = 5  # previous level or building


direction = {
    "north": np.array([0, -1]),
    "east": np.array([1, 0]),
    "south": np.array([0, 1]),
    "west": np.array([-1, 0]),
}


class MeetingRoom(Env):
    """Find the meeting room! It is located on a specific floor inside a specific building.

    :param n_buildings:
    :param n_floors:
    :param floor_shape: (width, height)
    :param render_mode:
    :param walls_fixed: if True, wall positions/floor maps remain the same over different
        episodes AND the walls are the same on all floors of a building
    """

    action_space = spaces.Discrete(len(Action))
    metadata = {
        'render.modes': ['human', 'rgb_array'],
        'video.frames_per_second': 10
    }

    map: Dict[int, Dict[Any, Any]]
    current_pos: np.array
    previous_pos: np.array
    target: np.array

    def __init__(self,
                 n_buildings: int = 4,
                 n_floors: int = 4,
                 floor_shape: [int, int] = None,
                 max_steps: int = 1000,
                 render_mode: str = "human",
                 walls_fixed: bool = False):
        if floor_shape is None:
            floor_shape = np.array([11, 11], dtype=int)
        else:
            floor_shape = np.asarray(floor_shape)

        self.render_mode = render_mode
        self.n_buildings = n_buildings
        self.n_floors = n_floors
        assert np.all(floor_shape[0] >= 5)
        self.floor_shape = floor_shape
        self.walls_fixed = walls_fixed

        obs_len = 9 if self.walls_fixed else 9 + PLAYER_VIEW_SIZE ** 2
        self.observation_space = spaces.Box(0, np.max(floor_shape), shape=(obs_len,), dtype=np.int32)

        self.map_generated = False
        self.terminated = False
        self.n_steps = 0
        self.max_steps = max_steps

        self._current_distance_to_target = -1
        self._previous_distance_to_target = -1
        self._best_distance_to_target = -1
        self._previous_best_distance_to_target = -1

        # Setup rendering
        self.window_shape = self.floor_shape * (FIELD_SIZE + BORDER_WIDTH) + 2 * BORDER_WIDTH + 2 * MARGIN
        self.window_shape[1] += self.n_floors * FLOOR_HEIGHT + MARGIN
        self.window = pygame.display.set_mode(self.window_shape)
        if self.render_mode == "human":
            self._init_pygame()

    def _generate_map(self):
        self.map = dict()
        for b in range(self.n_buildings):
            elevator_position = randint(1, self.floor_shape - 1)
            entrance_position = self._generate_entrance_position()
            self.map[b] = {"elevator": elevator_position,
                           "entrance": entrance_position}

            floor_base_map = self._generate_floor_map()

            for f in range(self.n_floors):
                if self.walls_fixed:
                    floor_map = floor_base_map.copy()
                else:
                    floor_map = self._generate_floor_map()

                # Remove walls in special cases
                if f == 0:
                    entrance_x, entrance_y = entrance_position
                    floor_map[entrance_x, entrance_y] = 0
                    c_dir = self._get_center_direction(*entrance_position)
                    floor_map[entrance_x + c_dir[0], entrance_y + c_dir[1]] = 0

                elevator_x, elevator_y = elevator_position
                floor_map[elevator_x, elevator_y] = 0
                if self._surrounded_by_walls(*elevator_position, floor_map):
                    c_dir = self._get_center_direction(*elevator_position)
                    floor_map[elevator_x + c_dir[0], elevator_y + c_dir[1]] = 0

                self.map[b][f] = floor_map

    def _generate_floor_map(self):
        """Creates an array encoding the floor's walls. Walls are represented
        by 1, empty space by 0."""
        floor_map = np.zeros(self.floor_shape)

        # Surrounding walls
        floor_map[[0, -1]] = 1
        floor_map[:, [0, -1]] = 1

        # Vertical wall
        wall_v = randint(2, self.floor_shape[1] - 2)
        floor_map[wall_v] = 1

        # Horizontal wall
        wall_h = randint(2, self.floor_shape[0] - 2, size=2)
        floor_map[:wall_v, wall_h[0]] = 1
        floor_map[wall_v:, wall_h[1]] = 1

        # Doorways
        # north
        n_choices = list(range(1, np.min(wall_h)))
        door_pos = np.random.choice(n_choices)
        floor_map[wall_v, door_pos] = 0

        # east
        n_choices = list(range(wall_v + 1, self.floor_shape[0] - 1))
        door_pos = np.random.choice(n_choices)
        floor_map[door_pos, wall_h[1]] = 0

        # south
        n_choices = list(range(np.max(wall_h) + 1, self.floor_shape[1] - 1))
        door_pos = np.random.choice(n_choices)
        floor_map[wall_v, door_pos] = 0

        # west
        n_choices = list(range(1, wall_v))
        door_pos = np.random.choice(n_choices)
        floor_map[door_pos, wall_h[0]] = 0

        return floor_map

    def _generate_entrance_position(self):
        # Pick one of the four sides of the building (0 north, 1 east etc.)
        side = randint(0, 4)

        # Determine entrance position inside wall
        wall_length = self.floor_shape[0] if side % 2 else self.floor_shape[1]
        pos_in_wall = randint(1, wall_length - 1)

        # Return final entrance door position
        if side == 0:
            x, y = 0, pos_in_wall
        elif side == 1:
            x, y = pos_in_wall, self.floor_shape[1] - 1
        elif side == 2:
            x, y = self.floor_shape[0] - 1, pos_in_wall
        else:
            x, y = pos_in_wall, 0
        return np.array([x, y], dtype=int)

    def reset(
            self,
            *,
            seed: int = None,
            options: Dict[str, Any] = None,
    ) -> Tuple[ObsType, Dict[str, Any]]:
        if not self.map_generated or not self.walls_fixed:
            self._generate_map()
            self.map_generated = True
        self.current_pos = self._pick_random_position()
        self.previous_pos = None
        target = self._pick_random_position()
        while np.all(target == self.current_pos):
            target = self._pick_random_position()
        self.target = target
        self._best_distance_to_target = np.inf
        self.update_current_distance_to_target()
        self._previous_best_distance_to_target = None
        self._previous_distance_to_target = None
        self.terminated = False
        self.n_steps = 0
        if self.render_mode == "human":
            self.render()
        return self._get_observation(), self._get_info()

    def _pick_random_position(self):
        b = randint(self.n_buildings)
        f = randint(self.n_floors)

        # Find valid position on floor, not occupied by a wall
        floor_map = self.map[b][f]
        while True:
            x, y = randint(1, self.floor_shape - 2, size=2)
            if not floor_map[x, y]:  # empty spot
                break

        return np.array([b, f, x, y])

    def _get_observation(self):
        obs = [
            self.target - self.current_pos,
            self._get_current_elevator() - self.current_pos[2:4],
            self._get_current_entrance() - self.current_pos[2:4],
            self.current_pos[1],
        ]
        if not self.walls_fixed:
            obs.append(self._get_player_view().flatten())
        return np.hstack(obs)

    def _get_player_view(self):
        floor_map = self._get_current_floor_map()
        x, y = self.current_pos[2:4]
        view_distance = PLAYER_VIEW_SIZE // 2
        view = floor_map[
               max(x - view_distance, 0):x + view_distance + 1,
               max(y - view_distance, 0):y + view_distance + 1
               ]
        padded_view = np.ones((PLAYER_VIEW_SIZE, PLAYER_VIEW_SIZE))
        x_p = max(view_distance - x, 0)
        y_p = max(view_distance - y, 0)
        padded_view[x_p: x_p + view.shape[0], y_p: y_p + view.shape[1]] = view
        return padded_view

    def _get_info(self):
        return dict()

    def step(self, action: ActType) -> Tuple[ObsType, SupportsFloat, bool, bool, Dict[str, Any]]:
        self._act(Action(action))
        target_reached = np.all(self.current_pos == self.target)
        obs = self._get_observation()
        reward = self._get_reward(target_reached)
        truncated = self.n_steps >= self.max_steps
        self.terminated |= target_reached | truncated
        info = self._get_info()
        self.n_steps += 1
        return obs, reward, self.terminated, truncated, info

    @property
    def current_distance_to_target(self):
        return self._current_distance_to_target

    @property
    def previous_distance_to_target(self):
        return self._previous_distance_to_target

    def update_current_distance_to_target(self):
        self._previous_distance_to_target = self._current_distance_to_target
        self._current_distance_to_target = self._distance(self.current_pos, self.target)
        self._best_distance_to_target = min(self._best_distance_to_target, self.current_distance_to_target)

    def _get_reward(self, target_reached: bool):
        """Compares previous position with current position and gives a reward
        according to the change of the distance to the target. Penalizes non-movement."""
        distance_diff = self.previous_distance_to_target - self.current_distance_to_target
        if self.target[0] == self.current_pos[0]:  # player in target building
            floor_diff = abs(self.target[1] - self.previous_pos[1]) - abs(self.target[1] - self.current_pos[1])
        else:
            floor_diff = self.previous_pos[1] - self.current_pos[1]
        floor_switch_bonus = floor_diff * 2
        if distance_diff < 0:
            distance_diff *= 2
        distance_reward = distance_diff / 2

        # TODO: Give only reward on best distance improvement

        if self.previous_pos is not None and np.all(self.previous_pos == self.current_pos):
            rest_penalty = -1
        else:
            rest_penalty = 0
        return distance_reward + floor_switch_bonus

    def _distance(self, pos1, pos2) -> float:
        """Returns the number of steps it needs at least to get from pos1 to pos2."""
        if np.all(pos1 == pos2):
            return 0

        b1, f1, x1, y1 = pos1
        b2, f2, x2, y2 = pos2

        if b1 == b2:
            return self._same_building_distance(pos1, pos2)
        else:
            entrance1 = self.map[b1]["entrance"]
            distance_entrance = self._same_building_distance(pos1, (b1, 0, *entrance1))
            building_diff = abs(b2 - b1)
            entrance2 = self.map[b2]["entrance"]
            distance_target = self._same_building_distance((b2, 0, *entrance2), pos2)
            return distance_entrance + building_diff + distance_target

    def _floor_distance(self, pos1, pos2, floor_map) -> float:
        """Applies Dijkstra to compute the distance (min #steps) from pos1 to pos2 that lie
        on the same floor, given a specific floor map."""
        x1, y1 = pos1
        x2, y2 = pos2

        assert not floor_map[x1, y1] and not floor_map[x2, y2]

        if x1 == x2 and y1 == y2:
            return 0

        distance_map = np.zeros(floor_map.shape, dtype=float) - 1
        distance_map[x1, y1] = 0.0
        queue = Queue()
        queue.put((x1, y1))
        while not queue.empty():
            x, y = queue.get()
            d = distance_map[x, y]
            neighbors = self._get_neighboring_empty_fields(x, y, floor_map)
            for (n_x, n_y) in neighbors:
                if n_x == x2 and n_y == y2:
                    return d + 1
                elif distance_map[n_x, n_y] == -1:
                    distance_map[n_x, n_y] = d + 1
                    queue.put((n_x, n_y))

        # for x in range(distance_map.shape[0]):
        #     for y in range(distance_map.shape[1]):
        #         d = distance_map[x, y]
        #         if d != -1:
        #             label_x = MARGIN + BORDER_WIDTH + x * (FIELD_SIZE + BORDER_WIDTH)
        #             label_y = MARGIN + BORDER_WIDTH + y * (FIELD_SIZE + BORDER_WIDTH)
        #             draw_label(self.window, str(int(d)), (label_x, label_y), pygame.font.SysFont('Calibri', 16))

        return -1.0

    def _same_building_distance(self, pos1, pos2) -> float:
        b1, f1, x1, y1 = pos1
        b2, f2, x2, y2 = pos2
        if f1 == f2:
            return self._floor_distance((x1, y1), (x2, y2), self.map[b1][f1])
        else:
            elevator = self.map[b1]["elevator"]
            d_to_elevator = self._floor_distance((x1, y1), elevator, self.map[b1][f1])
            floor_diff = abs(f2 - f1)
            d_to_target = self._floor_distance(elevator, (x2, y2), self.map[b1][f2])
            return d_to_elevator + floor_diff + d_to_target

    def _get_neighboring_empty_fields(self, x, y, floor_map):
        neighbors = []
        position = np.array([x, y])
        for d in direction.values():
            neighbor = position + d
            if np.all(0 <= neighbor) and np.all(neighbor < self.floor_shape) and not floor_map[
                neighbor[0], neighbor[1]]:
                neighbors.append(neighbor)
        return neighbors

    def _act(self, action: Action):
        self.previous_pos = self.current_pos.copy()
        b, f, x, y = self.current_pos
        elevator = self._get_current_elevator()
        entrance = self._get_current_entrance()

        if action in [Action.NORTH, Action.EAST, Action.SOUTH, Action.WEST]:
            next_pos = None
            if action == Action.NORTH:
                    next_pos = self.current_pos[2:4] + direction["north"]
            elif action == Action.EAST:
                    next_pos = self.current_pos[2:4] + direction["east"]
            elif action == Action.SOUTH:
                    next_pos = self.current_pos[2:4] + direction["south"]
            elif action == Action.WEST:
                    next_pos = self.current_pos[2:4] + direction["west"]

            if self._is_valid_next_step(*next_pos):
                self.current_pos[2:4] = next_pos

        elif action in [Action.NEXT, Action.PREV]:
            if np.all(self.current_pos[2:4] == elevator):
                if action == Action.NEXT and f < self.n_floors - 1:
                    self.current_pos[1] += 1
                elif action == Action.PREV and f > 0:
                    self.current_pos[1] -= 1

            elif np.all(self.current_pos[2:4] == entrance) and f == 0:
                if action == Action.NEXT and b < self.n_buildings - 1:
                    self.current_pos[0] += 1
                elif action == Action.PREV and b > 0:
                    self.current_pos[0] -= 1
                entrance = self._get_current_entrance()
                self.current_pos[2:4] = entrance

        self.update_current_distance_to_target()

    def _is_valid_next_step(self, x, y):
        floor_map = self._get_current_floor_map()
        return 0 <= x < self.floor_shape[0] and \
            0 <= y < self.floor_shape[1] and not floor_map[x, y]

    def _get_current_floor_map(self):
        b, f, _, _ = self.current_pos
        return self.map[b][f]

    def _get_current_entrance(self):
        return self.map[self.current_pos[0]]["entrance"]

    def _get_current_elevator(self):
        return self.map[self.current_pos[0]]["elevator"]

    def _init_pygame(self):
        pygame.init()
        pygame.display.set_caption("Meeting Room")
        self.clock = pygame.time.Clock()

    def render(self):
        self._render_frame()
        if self.render_mode == "rgb_array":
            return pygame.surfarray.array3d(self.window).transpose((1, 0, 2))
        elif self.previous_pos is None or not np.all(self.current_pos == self.previous_pos):
            self._display_frame()
            self.clock.tick(self.metadata["video.frames_per_second"])

    def _render_frame(self):
        self.window.fill((255, 255, 255))  # clear the entire window
        self._render_grid()
        self._render_buildings()

    def _render_grid(self):
        b, f, _, _ = self.current_pos
        floor_map = self._get_current_floor_map()
        height, width = floor_map.shape

        # Draw grid background
        grid_width = width * (FIELD_SIZE + BORDER_WIDTH) + BORDER_WIDTH
        grid_height = height * (FIELD_SIZE + BORDER_WIDTH) + BORDER_WIDTH
        pygame.draw.rect(self.window, (0, 0, 0), [MARGIN, MARGIN, grid_width, grid_height])

        entrance = self._get_current_entrance()
        elevator = self._get_current_elevator()

        # Draw all fields
        for x in range(width):
            for y in range(height):
                if floor_map[x, y] == 0:  # empty field
                    self._render_field(x, y, color="white")
                else:  # wall
                    self._render_field(x, y, color="#555555")
                if np.all((x, y) == entrance) and f == 0:  # entrance
                    self._render_entrance(x, y)
                if np.all((x, y) == elevator):  # elevator
                    self._render_elevator(x, y)
                if np.all((b, f, x, y) == self.target) and not self.terminated:  # target
                    self._render_target(x, y)
                if np.all((x, y) == self.current_pos[2:4]):  # player
                    self._render_player(x, y)

    def _render_field(self, x_coord, y_coord, color=(255, 255, 255)):
        x = MARGIN + BORDER_WIDTH + x_coord * (FIELD_SIZE + BORDER_WIDTH)
        y = MARGIN + BORDER_WIDTH + y_coord * (FIELD_SIZE + BORDER_WIDTH)
        pygame.draw.rect(self.window, color, [x, y, FIELD_SIZE, FIELD_SIZE])

    def _render_player(self, x_coord, y_coord):
        x, y = self._get_field_center(x_coord, y_coord)
        pygame.draw.circle(self.window, "#000000", [x, y], 0.35 * FIELD_SIZE)
        pygame.draw.circle(self.window, PLAYER_COLOR, [x, y], 0.3 * FIELD_SIZE)

    def _render_target(self, x_coord, y_coord):
        center = np.asarray(self._get_field_center(x_coord, y_coord))
        for i in range(5):
            angle = 2 * np.pi * i / 5
            rot = get_rotation_matrix(angle)
            shape = np.array([[-0.18, 0],
                              [0, -0.4],
                              [0.18, 0]]) * FIELD_SIZE
            polygon = center + np.dot(rot, shape.T).T
            pygame.draw.polygon(self.window, TARGET_COLOR, polygon)

    def _render_elevator(self, x_coord, y_coord):
        self._render_field(x_coord, y_coord, color=ELEVATOR_COLOR)
        center = np.asarray(self._get_field_center(x_coord, y_coord))
        shape = np.array([[-0.3, -0.1],
                          [0, -0.4],
                          [0.3, -0.1]]) * FIELD_SIZE
        up = center + shape
        down = center + shape * np.asarray([1, -1])
        pygame.draw.polygon(self.window, "white", up)
        pygame.draw.polygon(self.window, "white", down)

    def _render_entrance(self, x_coord, y_coord):
        self._render_field(x_coord, y_coord, color=ENTRANCE_COLOR)
        center = np.asarray(self._get_field_center(x_coord, y_coord))
        dir = self._get_center_direction(x_coord, y_coord) * 0.25 * FIELD_SIZE
        draw_arrow(self.window,
                   start_pos=center - dir,
                   end_pos=center + dir,
                   tip_width=int(0.4 * FIELD_SIZE),
                   color="white",
                   width=int(0.15 * FIELD_SIZE))

    def _render_buildings(self):
        margin_top = 2 * MARGIN + self.floor_shape[1] * (FIELD_SIZE + BORDER_WIDTH) + BORDER_WIDTH
        margin_left = MARGIN

        # Ground line
        start_pos = [margin_left - 30, margin_top + self.n_floors * (FLOOR_HEIGHT - BORDER_WIDTH)]
        end_pos = [margin_left + self.n_buildings * (BUILDING_WIDTH + 30), start_pos[1]]
        pygame.draw.line(self.window, "#000000", start_pos, end_pos, width=BORDER_WIDTH)

        # Buildings
        for b in range(self.n_buildings):
            for f in reversed(range(self.n_floors)):
                x = margin_left + b * (BUILDING_WIDTH + 30)
                y = margin_top + (self.n_floors - f - 1) * (FLOOR_HEIGHT - BORDER_WIDTH)
                w = BUILDING_WIDTH
                h = FLOOR_HEIGHT
                pygame.draw.rect(self.window, "#000000", [x, y, w, h])
                if b == self.current_pos[0] and f == self.current_pos[1]:
                    floor_color = PLAYER_COLOR
                elif b == self.target[0] and f == self.target[1]:
                    floor_color = TARGET_COLOR
                else:
                    floor_color = "#FFFFFF"
                pygame.draw.rect(self.window, floor_color, [x + BORDER_WIDTH, y + BORDER_WIDTH,
                                                            w - 2 * BORDER_WIDTH, h - 2 * BORDER_WIDTH])

    def _get_center_direction(self, x, y):
        width, height = self.floor_shape
        diag_1 = x * (height - 1) / (width - 1)
        diag_2 = (height - 1) - x * (height - 1) / (width - 1)
        if y < diag_1:
            if y < diag_2:
                return direction["south"]
            else:
                return direction["west"]
        else:
            if y > diag_2:
                return direction["north"]
            else:
                return direction["east"]

    def _get_field_center(self, x_coord, y_coord):
        x = MARGIN + BORDER_WIDTH + x_coord * (FIELD_SIZE + BORDER_WIDTH) + FIELD_SIZE // 2
        y = MARGIN + BORDER_WIDTH + y_coord * (FIELD_SIZE + BORDER_WIDTH) + FIELD_SIZE // 2
        return x, y

    def _surrounded_by_walls(self, x, y, floor_map):
        position = np.asarray([x, y])
        for d in list(direction.values()):
            x_adjacent, y_adjacent = position + d
            if 0 <= x_adjacent < self.floor_shape[0] and 0 <= y_adjacent < self.floor_shape[1]:
                if not floor_map[x_adjacent, y_adjacent]:
                    return False
        return True

    def _display_frame(self):
        pygame.display.flip()
        pygame.event.pump()

    def get_keys_to_action(self):
        return {
            (pygame.K_w,): 0,  # north
            (pygame.K_d,): 1,  # east
            (pygame.K_s,): 2,  # south
            (pygame.K_a,): 3,  # west
            (pygame.K_PLUS,): 4,  # next
            (pygame.K_MINUS,): 5,  # prev
        }


def get_rotation_matrix(rad: float):
    return np.array([[np.cos(rad), -np.sin(rad)], [np.sin(rad), np.cos(rad)]])