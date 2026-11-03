import asyncio
import random
import time

PALETTE = {
    "red": [255, 0, 0],
    "orange": [255, 90, 0],
    "yellow": [255, 200, 0],
    "green": [0, 255, 0],
    "cyan": [0, 255, 190],
    "blue": [0, 90, 255],
    "purple": [140, 0, 255],
    "pink": [255, 0, 140],
}
SNAP = ["red", "orange", "yellow", "green", "blue", "purple"]
WHITE = [255, 255, 255]
GREY = [60, 60, 60]
DARK = [0, 0, 0]
TITLES = {
    "tug": "Tug of war",
    "quickdraw": "Reaction duel",
    "snap": "Color snap",
    "potato": "Hot potato",
    "rps": "Rock paper scissors",
    "simon": "Simon says",
    "memory": "Color memory",
    "reflex": "Reaction test",
    "whack": "Whack-a-color",
    "stop": "Stop the light",
}
SOLO = {"simon", "memory", "reflex", "whack", "stop"}
LOWER_BETTER = {"reflex"}
BEATS = {"rock": "scissors", "paper": "rock", "scissors": "paper"}


def blend(a, b, t):
    return [round(x + (y - x) * t) for x, y in zip(a, b)]


def is_color(c):
    return isinstance(c, list) and len(c) == 3 and all(isinstance(v, (int, float)) for v in c)


def number(value, lo, hi):
    try:
        return max(lo, min(hi, int(value)))
    except (TypeError, ValueError):
        return None


class Over(Exception):
    pass


class Match:
    def __init__(self, arcade, game, host):
        self.arcade = arcade
        self.game = game
        self.players = [host]
        self.colors = [host.color, None]
        self.score = [0, 0]
        self.phase = "waiting"
        self.round = 0
        self.info = {}
        self.winner = None
        self.reason = ""
        self.over = False
        self.wake = asyncio.Event()

    def begin(self, guest):
        self.players.append(guest)
        order = [guest.color, "blue", "red", "green", "yellow", "purple"]
        self.colors[1] = next(c for c in order if c != self.colors[0])

    def name(self, i):
        return self.players[i].name

    def rgb(self, i):
        return PALETTE[self.colors[i]]

    def light(self, color):
        self.arcade.light(color)

    def show(self, phase, **info):
        self.phase = phase
        self.info = info
        self.arcade.changed()

    def poke(self):
        self.wake.set()

    async def wait(self, timeout, done=lambda: False):
        end = time.monotonic() + timeout
        while True:
            if self.over:
                raise Over
            if done():
                return True
            left = end - time.monotonic()
            if left <= 0:
                return False
            self.wake.clear()
            try:
                await asyncio.wait_for(self.wake.wait(), left)
            except asyncio.TimeoutError:
                pass

    async def sleep(self, seconds):
        await self.wait(seconds)

    async def flash(self, color, times=2, on=0.22, off=0.16):
        for _ in range(times):
            self.light(color)
            await self.sleep(on)
            self.light(DARK)
            await self.sleep(off)
        self.light(color)

    async def countdown(self):
        for n in (3, 2, 1):
            self.show("countdown", n=n)
            self.light(WHITE if n == 1 else GREY)
            await self.sleep(0.7)
            self.light(DARK)
            await self.sleep(0.3)

    def by_score(self):
        if self.score[0] != self.score[1]:
            self.winner = 0 if self.score[0] > self.score[1] else 1

    def forfeit(self, i):
        self.winner = 1 - i
        self.reason = f"{self.name(i)} left"
        self.over = True
        self.poke()

    def view(self):
        return {
            "kind": "match",
            "game": self.game,
            "phase": self.phase,
            "round": self.round,
            "players": [p.public() for p in self.players],
            "colors": self.colors,
            "score": self.score,
            "winner": self.winner,
            "reason": self.reason,
            **self.info,
            **self.extra(),
        }

    def extra(self):
        return {}

    def act(self, i, data):
        pass

    async def play(self):
        pass


class Tug(Match):
    async def play(self):
        self.rope = 0
        self.taps = [[], []]
        self.ends = time.monotonic() + 30
        self.pull()
        await self.wait(30, lambda: abs(self.rope) >= 100)
        if self.rope:
            self.winner = 0 if self.rope < 0 else 1
        else:
            self.reason = "Nobody moved the rope"

    def pull(self):
        self.show("play", rope=self.rope)
        self.light(blend(self.rgb(0), self.rgb(1), (self.rope + 100) / 200))

    def extra(self):
        return {"left": max(0, round(self.ends - time.monotonic(), 1))} if self.phase == "play" else {}

    def act(self, i, data):
        if self.phase != "play" or data.get("a") != "tap":
            return
        now = time.monotonic()
        recent = [t for t in self.taps[i] if now - t < 1]
        if len(recent) >= 15:
            return
        self.taps[i] = recent + [now]
        self.rope = max(-100, min(100, self.rope + (3 if i else -3)))
        self.pull()
        if abs(self.rope) >= 100:
            self.poke()


class Quickdraw(Match):
    async def play(self):
        while max(self.score) < 3:
            self.round += 1
            self.taps, self.early = {}, None
            self.show("ready")
            self.light(GREY)
            await self.sleep(1.2)
            self.show("wait")
            self.light([255, 0, 0])
            await self.wait(random.uniform(2, 5), lambda: self.early is not None)
            if self.early is None:
                self.show("go")
                self.light([0, 255, 0])
                await self.wait(3, lambda: len(self.taps) == 2 or self.early is not None)
            if self.early is not None:
                win = 1 - self.early
                text = f"{self.name(self.early)} tapped too early"
            elif self.taps:
                win = min(self.taps, key=self.taps.get)
                text = f"{self.name(win)} was faster"
            else:
                win, text = None, "Nobody tapped"
            if win is not None:
                self.score[win] += 1
            self.show("result", text=text, ms=[self.taps.get(0), self.taps.get(1)])
            if win is not None:
                await self.flash(self.rgb(win))
            await self.sleep(1.6)
        self.by_score()

    def act(self, i, data):
        if data.get("a") != "tap" or data.get("round") != self.round:
            return
        if self.phase == "wait" or (self.phase == "go" and data.get("early")):
            if self.early is None:
                self.early = i
                self.poke()
        elif self.phase == "go" and i not in self.taps:
            ms = number(data.get("ms"), 0, 5000)
            if ms is not None and ms >= 60:
                self.taps[i] = ms
                self.poke()


class Snap(Match):
    async def play(self):
        while max(self.score) < 5:
            self.round += 1
            light = random.choice(SNAP)
            word = random.choice([c for c in SNAP if c != light])
            rule = random.choice(["light", "word"])
            answer = light if rule == "light" else word
            ink = random.choice([c for c in SNAP if c not in (light, word)])
            self.answers = {}
            self.show("show", rule=rule, light=light, word=word, ink=ink,
                      options=random.sample(SNAP, len(SNAP)), picked=[False, False])
            self.light(PALETTE[light])
            await self.wait(6, lambda: len(self.answers) == 2)
            right = {i: ms for i, (c, ms) in self.answers.items() if c == answer}
            if right:
                win = min(right, key=right.get)
                self.score[win] += 1
                text = f"{self.name(win)} got it in {right[win]} ms"
            else:
                text = f"Nobody got it. The answer was {answer}"
            self.show("result", text=text)
            await self.sleep(1.8)
        self.by_score()

    def act(self, i, data):
        if self.phase != "show" or data.get("a") != "pick" or data.get("round") != self.round or i in self.answers:
            return
        ms = number(data.get("ms"), 60, 10000)
        if data.get("c") not in SNAP or ms is None:
            return
        self.answers[i] = (data["c"], ms)
        self.info["picked"][i] = True
        self.arcade.changed()
        self.poke()


class Potato(Match):
    async def play(self):
        while max(self.score) < 2:
            self.round += 1
            self.holder = random.randrange(2)
            self.hand_over()
            await self.sleep(random.uniform(10, 22))
            loser = self.holder
            self.score[1 - loser] += 1
            self.show("boom", text=f"BOOM. {self.name(loser)} was holding it")
            await self.flash(WHITE, 3, 0.12, 0.1)
            self.light(self.rgb(1 - loser))
            await self.sleep(2)
        self.by_score()

    def hand_over(self):
        options = random.sample([1, 2, 3, 4], 4)
        self.show("play", holder=self.holder, target=random.choice(options), options=options)
        self.light(self.rgb(self.holder))

    def act(self, i, data):
        if self.phase != "play" or i != self.holder or data.get("a") != "pick":
            return
        if data.get("n") == self.info["target"]:
            self.holder = 1 - i
        self.hand_over()


class Rps(Match):
    async def play(self):
        while max(self.score) < 3:
            self.round += 1
            self.picks = {}
            self.show("pick", picked=[False, False])
            self.light(GREY)
            await self.wait(20, lambda: len(self.picks) == 2)
            a, b = self.picks.get(0), self.picks.get(1)
            if a and b and a != b:
                win = 0 if BEATS[a] == b else 1
                text = f"{self.picks[win].title()} beats {self.picks[1 - win]}. Point to {self.name(win)}"
            elif a and b:
                win, text = None, "Same pick, nobody scores"
            elif a or b:
                win = 0 if a else 1
                text = f"{self.name(1 - win)} didn't pick in time"
            else:
                win, text = None, "Nobody picked"
            if win is not None:
                self.score[win] += 1
            self.show("reveal", picks=[a, b], text=text)
            self.light(self.rgb(win) if win is not None else WHITE)
            await self.sleep(2.4)
        self.by_score()

    def act(self, i, data):
        if self.phase != "pick" or data.get("a") != "pick" or data.get("round") != self.round or i in self.picks:
            return
        if data.get("c") not in BEATS:
            return
        self.picks[i] = data["c"]
        self.info["picked"][i] = True
        self.arcade.changed()
        self.poke()


MATCHES = {"tug": Tug, "quickdraw": Quickdraw, "snap": Snap, "potato": Potato, "rps": Rps}


class Solo:
    def __init__(self, game, player):
        self.game = game
        self.player = player
        self.score = 0
        self.seen = time.monotonic()

    def view(self):
        return {"kind": "solo", "game": self.game, "players": [self.player.public()], "score": self.score}


class Arcade:
    def __init__(self, hub, saved):
        self.hub = hub
        self.activity = None
        self.scores = saved.get("scores") or {}
        self.wins = saved.get("wins") or {}
        self.event = {"id": 0, "text": ""}
        self.glow = None
        self.nudge_at = 0.0
        self.effect = None
        self.tasks = set()

    @property
    def busy(self):
        return self.activity is not None or bool(self.effect and not self.effect.done())

    def spawn(self, coro):
        task = asyncio.ensure_future(coro)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return task

    def stop(self):
        for task in list(self.tasks):
            task.cancel()

    def changed(self):
        self.hub.arcade_changed()

    def announce(self, text):
        self.event = {"id": self.event["id"] + 1, "text": text}
        self.changed()

    def light(self, color):
        color = [max(0, min(255, int(c))) for c in color]
        self.glow = color
        self.hub.light.request({**self.hub.state, "on": True, "mode": "color", "color": color, "brightness": 100})
        self.changed()

    def restore(self):
        self.glow = None
        self.hub.light.request(self.hub.state)
        self.changed()

    def view(self):
        return {
            "t": "arcade",
            "activity": self.activity.view() if self.activity else None,
            "scores": self.scores,
            "wins": self.wins,
            "event": self.event,
            "glow": self.glow,
            "online": [s.public() for s in self.hub.sessions],
        }

    def handle(self, s, data):
        a = data.get("a")
        act = self.activity
        game = data.get("game")
        if a == "nudge":
            self.nudge(s)
        elif a == "challenge" and game in MATCHES and not self.busy:
            self.activity = MATCHES[game](self, game, s)
            self.hub.light.request(self.hub.state)
            self.announce(f"{s.name} wants to play {TITLES[game]}")
        elif a == "join" and isinstance(act, Match) and act.phase == "waiting" and s is not act.players[0]:
            act.begin(s)
            act.phase = "countdown"
            self.spawn(self.run_match(act))
        elif a == "leave":
            self.left(s)
        elif a == "solo" and game in SOLO and not self.busy:
            self.activity = Solo(game, s)
            self.hub.light.request(self.hub.state)
            self.spawn(self.watch_solo(self.activity))
            self.changed()
        elif isinstance(act, Solo) and act.player is s:
            act.seen = time.monotonic()
            if a == "light" and is_color(data.get("c")):
                self.light(data["c"])
            elif a == "progress":
                act.score = number(data.get("score"), 0, 100000) or 0
                self.changed()
            elif a == "end":
                self.end_solo(act, data.get("score") if data.get("record") else None)
        elif isinstance(act, Match) and s in act.players and act.phase != "waiting" and not act.over:
            act.act(act.players.index(s), data)

    def left(self, s):
        act = self.activity
        if isinstance(act, Match) and s in act.players:
            if act.phase == "waiting":
                self.activity = None
                self.restore()
            elif not act.over:
                act.forfeit(act.players.index(s))
        elif isinstance(act, Solo) and act.player is s:
            self.end_solo(act, None)

    async def run_match(self, m):
        try:
            await m.countdown()
            await m.play()
        except Over:
            pass
        except Exception as e:
            print(f"Game error in {m.game}: {e!r}")
        m.over = True
        m.show("over")
        if m.winner is not None:
            name = m.name(m.winner)
            self.wins[name] = self.wins.get(name, 0) + 1
            self.hub.save_soon()
            self.announce(f"{name} won {TITLES[m.game]}" + (f" ({m.reason})" if m.reason else ""))
            await self.flash(m.rgb(m.winner), 4)
        else:
            self.announce(f"{TITLES[m.game]} ended in a draw")
        await asyncio.sleep(3)
        if self.activity is m:
            self.activity = None
            self.restore()

    async def flash(self, color, times=3):
        for _ in range(times):
            self.light(color)
            await asyncio.sleep(0.28)
            self.light(DARK)
            await asyncio.sleep(0.22)
        self.light(color)

    async def watch_solo(self, solo):
        while self.activity is solo:
            await asyncio.sleep(5)
            if self.activity is solo and time.monotonic() - solo.seen > 90:
                self.end_solo(solo, None)

    def end_solo(self, solo, score):
        if self.activity is not solo:
            return
        self.activity = None
        self.restore()
        score = number(score, 0, 100000)
        lower = solo.game in LOWER_BETTER
        if score is None or (lower and score <= 0):
            return
        name = solo.player.name
        best = self.scores.get(solo.game)
        better = best is None or (score < best["score"] if lower else score > best["score"])
        if better:
            self.scores[solo.game] = {"score": score, "name": name}
            self.hub.save_soon()
        unit = " ms" if lower else ""
        self.announce(f"{name} got {score}{unit} in {TITLES[solo.game]}" + (", a new best" if better else ""))

    def nudge(self, s):
        now = time.monotonic()
        if self.busy or now - self.nudge_at < 4:
            return
        self.nudge_at = now
        self.effect = self.spawn(self.nudge_flash(PALETTE[s.color]))
        self.announce(f"{s.name} sent a nudge")

    async def nudge_flash(self, color):
        self.hub.light.request(self.hub.state)
        await self.hub.light.ready()
        await self.flash(color, 3)
        await asyncio.sleep(0.4)
        self.restore()
