"""Run Brong commands from speech and return what they actually say."""

import builtins
import difflib
import io
import json
import os
import queue
import re
import sys
import threading
import urllib.parse
import urllib.request
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "jarviscli"))

ANSI = re.compile(r"\x1b\[[0-9;]*m")
FILLER = re.compile(
    r"\b(please|jarvis|brong|hey|can you|could you|would you|i want to|i want|"
    r"i need to|i need|give me|tell me|show me|let me|play|start|for me)\b"
)
YES = {"yes", "yeah", "yep", "yup", "sure", "please", "ok", "okay", "go ahead", "do it"}
NO = {"no", "nope", "nah", "don't", "do not"}
BLOCKED = {
    "shutdown",
    "reboot_computer",
    "hibernate",
    "hybridsleep",
    "suspend",
    "log off",
    "screen off",
    "exit",
}
PHRASES = [
    (r"\b(dad jokes?)\b", "dadjoke"),
    (r"\b(chuck norris)\b", "joke chuck"),
    (r"\b(cat facts?)\b", "cat fact"),
    (r"\b(random facts?|tell me a fact)\b", "fact"),
    (r"\b(make me laugh|something funny|a joke)\b", "joke"),
    (r"\b(bored|nothing to do|something to do)\b", "bored"),
    (r"\b(what should i draw|drawing idea|art prompt)\b", "prompt"),
    (r"\b(what (should|can) i watch|what to watch|top media)\b", "topmedia"),
    (r"\b(mood music)\b", "mood music"),
    (r"\b(taste dive)\b", "taste dive"),
    (r"\b(rock paper scissors)\b", "rockpaperscissors"),
    (r"\b(tic tac toe)\b", "tic_tac_toe"),
    (r"\b(connect four)\b", "connect_four"),
    (r"\b(guess the number)\b", "guess_number_game"),
    (r"\b(word game)\b", "word_game"),
    (r"\b(cocktail|mix me a drink)\b", "drink"),
    (r"\b(food recipe|recipe for)\b", "food recipe"),
    (r"\b(workout|exercise)\b", "workout"),
    (r"\b(body mass|my bmi)\b", "bmi"),
    (r"\b(random password)\b", "random password"),
    (r"\b(random number)\b", "random number"),
    (r"\b(random list)\b", "random list"),
    (r"\b(qr code)\b", "qr"),
    (r"\b(my ip|ip address)\b", "ip"),
    (r"\b(system info|computer specs)\b", "systeminfo"),
    (r"\b(how much ram|check ram)\b", "check ram"),
    (r"\b(screenshot|screen shot|screen capture)\b", "screencapture"),
    (r"\b(open the camera|open camera)\b", "open camera"),
    (r"\b(bitcoin|crypto price|ethereum)\b", "cryptotracker"),
    (r"\b(stock price|share price)\b", "stock"),
    (r"\b(basketball)\b", "basketball"),
    (r"\b(cricket)\b", "cricket"),
    (r"\b(soccer|football score)\b", "soccer"),
    (r"\b(tennis)\b", "tennis"),
    (r"\b(hangman)\b", "hangman"),
    (r"\b(blackjack|black jack)\b", "blackjack"),
    (r"\b(roulette)\b", "roulette"),
    (r"\b(wordle)\b", "wordle"),
    (r"\b(translate)\b", "translate"),
    (r"\b(what time|the time is|clock)\b", "clock"),
    (r"\b(celsius|fahrenheit|convert temperature)\b", "tempconv"),
    (r"\b(convert currency|exchange rate)\b", "currencyconv"),
    (r"\b(how many miles|how many kilometers|convert length)\b", "lengthconv"),
]


class Cancelled(Exception):
    pass


def _get_json(url):
    request = urllib.request.Request(url, headers={"User-Agent": "Brong/1.0"})
    with urllib.request.urlopen(request, timeout=12) as response:
        return json.loads(response.read().decode("utf-8"))


WEATHER_CODES = {
    0: "clear",
    1: "mainly clear",
    2: "partly cloudy",
    3: "overcast",
    45: "foggy",
    48: "foggy",
    51: "light drizzle",
    53: "drizzle",
    55: "heavy drizzle",
    61: "light rain",
    63: "rain",
    65: "heavy rain",
    71: "light snow",
    73: "snow",
    75: "heavy snow",
    80: "rain showers",
    81: "rain showers",
    82: "heavy rain showers",
    95: "thunderstorms",
    96: "thunderstorms",
    99: "thunderstorms",
}


def _condition(code):
    return WEATHER_CODES.get(int(code), "unsettled")


def _clean(value):
    text = re.sub(r"\s+", " ", value or "").strip(" .,!?:;")
    return text


def _is_email(text):
    return re.search(r"\b(e-?mails?|gmail|inbox)\b", text) is not None


def _is_weather(text):
    return re.search(r"\b(weathers?|forecasts?|temperatures?)\b", text) is not None


def _city_from(text):
    match = re.search(r"\b(?:in|for|at|around)\s+([a-zA-Z][a-zA-Z .'\-]{1,40})$", text)
    if not match:
        return None
    return _clean(match.group(1))


def _geocode(city):
    query = urllib.parse.urlencode(
        {"name": city, "count": 1, "language": "en", "format": "json"}
    )
    payload = _get_json("https://geocoding-api.open-meteo.com/v1/search?" + query)
    results = payload.get("results") or []
    if not results:
        return None
    place = results[0]
    return {
        "name": place.get("name") or city,
        "latitude": place["latitude"],
        "longitude": place["longitude"],
        "timezone": place.get("timezone") or "auto",
    }


def _forecast(place):
    query = urllib.parse.urlencode(
        {
            "latitude": place["latitude"],
            "longitude": place["longitude"],
            "current": "temperature_2m,relative_humidity_2m,precipitation,weather_code,wind_speed_10m",
            "daily": "weather_code,temperature_2m_max,temperature_2m_min",
            "timezone": place["timezone"],
            "forecast_days": 4,
        }
    )
    return _get_json("https://api.open-meteo.com/v1/forecast?" + query)


def _weather(city, weekly=False):
    place = _geocode(city)
    if place is None:
        return {
            "say": "I couldn't find {0}. Say the city name.".format(city),
            "pending": "city",
            "city": None,
            "open": None,
            "card": None,
        }
    data = _forecast(place)
    if weekly:
        daily = data["daily"]
        parts = []
        rows = []
        start = 1 if len(daily["time"]) > 1 else 0
        for index in range(start, min(start + 3, len(daily["time"]))):
            day_name = datetime.strptime(daily["time"][index], "%Y-%m-%d").strftime("%A")
            sky = _condition(daily["weather_code"][index])
            low = round(daily["temperature_2m_min"][index])
            high = round(daily["temperature_2m_max"][index])
            parts.append("{0}, {1}, {2} to {3} degrees".format(day_name, sky, low, high))
            rows.append("{0}  {1}  {2}–{3}°".format(day_name[:3].upper(), sky, low, high))
        sentence = "The next few days in {0}: {1}.".format(place["name"], ". ".join(parts))
        card = {
            "kind": "forecast",
            "title": place["name"].upper(),
            "temp": "3 DAYS",
            "detail": "\n".join(rows),
            "meta": "",
        }
        pending = None
    else:
        current = data["current"]
        temp = round(current["temperature_2m"])
        humidity = round(current["relative_humidity_2m"])
        wind = round(current["wind_speed_10m"])
        sky = _condition(current["weather_code"])
        sentence = (
            "In {where} it is {temp} degrees and {sky}. "
            "Humidity is {humidity} percent, wind is {wind} kilometers per hour. "
            "Want the next few days?"
        ).format(where=place["name"], temp=temp, sky=sky, humidity=humidity, wind=wind)
        card = {
            "kind": "weather",
            "title": place["name"].upper(),
            "temp": "{0}°C".format(temp),
            "detail": sky.upper(),
            "meta": "HUM {0}%    WIND {1} KM/H".format(humidity, wind),
        }
        pending = "week"
    return {
        "say": sentence,
        "pending": pending,
        "city": place["name"],
        "open": None,
        "card": card,
    }


def _email():
    return {
        "say": "I can't read the messages from here. Opening your inbox.",
        "pending": None,
        "city": None,
        "open": "https://mail.google.com/mail/u/0/#inbox",
        "card": {
            "kind": "email",
            "title": "INBOX",
            "temp": "GMAIL",
            "detail": "Opening your inbox",
            "meta": "Sign in if Google asks",
        },
    }


def _agent_card(agent, detail):
    return {
        "kind": "answer",
        "title": agent,
        "temp": "",
        "detail": detail,
        "meta": "",
    }


def _wiki(topic):
    query = urllib.parse.urlencode(
        {
            "action": "opensearch",
            "search": topic,
            "limit": 1,
            "namespace": 0,
            "format": "json",
        }
    )
    request = urllib.request.Request(
        "https://en.wikipedia.org/w/api.php?" + query,
        headers={"User-Agent": "Brong/1.0 (local voice assistant)"},
    )
    with urllib.request.urlopen(request, timeout=8) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if len(payload) < 2 or not payload[1]:
        return None
    title = payload[1][0]
    summary = urllib.request.Request(
        "https://en.wikipedia.org/api/rest_v1/page/summary/" + urllib.parse.quote(title.replace(" ", "_")),
        headers={"User-Agent": "Brong/1.0 (local voice assistant)"},
    )
    with urllib.request.urlopen(summary, timeout=8) as response:
        page = json.loads(response.read().decode("utf-8"))
    extract = (page.get("extract") or "").strip()
    if not extract:
        return None
    sentence = extract.split(". ")[0].strip()
    if sentence and not sentence.endswith("."):
        sentence += "."
    return title, sentence


def _pack(say, pending=None, city=None, open_url=None, card=None, stop=False):
    result = {
        "say": say,
        "pending": pending,
        "city": city,
        "open": open_url,
        "card": card,
    }
    if stop:
        result["stop"] = True
    return result


class Engine(object):
    def __init__(self):
        self.boot_done = threading.Event()
        self.online = False
        self.boot_error = ""
        self.jarvis = None
        self.names = []
        self.name_set = set()
        self.lock = threading.Lock()
        self.ready = threading.Event()
        self.inbox = queue.Queue()
        self.output = []
        self.waiting = False
        self.running = False
        self.generation = 0
        self.worker_gen = 0
        self.current_command = ""
        self.suggestion = None
        self._real_input = builtins.input

    def start_boot(self):
        threading.Thread(target=self._boot, name="brong-boot", daemon=True).start()

    def health(self):
        return {
            "ready": self.online,
            "name": "Brong",
            "commands": len(self.names),
            "error": self.boot_error,
        }

    def ask(self, payload):
        if not self.boot_done.wait(50):
            return _pack("Brong is still coming online.")
        if not self.online:
            return _pack("Brong couldn't load its commands. {0}".format(self.boot_error))

        text = _clean(payload.get("text") or "")
        lowered = text.lower()
        pending = payload.get("pending")
        city = payload.get("city")
        if not lowered:
            return _pack("I didn't catch that.", pending, city)

        if self.waiting:
            return self._continue_plugin(lowered, text)

        if pending == "confirm" and self.suggestion:
            if lowered in YES or lowered.startswith("yes"):
                command = self.suggestion
                self.suggestion = None
                return self._run_and_collect(command)
            if lowered in NO or lowered.startswith("no"):
                self.suggestion = None
                return _pack("Okay.")

        if lowered in {"exit", "quit", "goodbye", "good bye", "stop listening", "go to sleep"}:
            return _pack("Standing by.", stop=True)

        if _is_email(lowered):
            return _email()

        if _is_weather(lowered):
            found = _city_from(lowered)
            if found:
                return _weather(found, weekly=("week" in lowered or "next few" in lowered))
            if pending == "city" and city:
                return _weather(city)
            return _pack("Which city?", "city", None)

        if pending == "city" and not self._resolve(lowered):
            if lowered in YES or lowered in NO:
                return _pack("Say the city name.", "city", None)
            return _weather(text)

        if pending == "week" and city:
            if lowered in YES or lowered.startswith("yes"):
                return _weather(city, weekly=True)
            if lowered in NO or lowered.startswith("no"):
                return _pack("Alright.", None, city)

        if re.search(r"\b(help|what can you do|your commands)\b", lowered):
            return self._help()

        spoken = self._conversation_agent(lowered)
        if spoken:
            return spoken

        command = self._resolve(lowered)
        if command:
            if self._blocked(command):
                return _pack("I won't do that from the voice screen.")
            self.suggestion = None
            return self._run_and_collect(command)

        known = self._knowledge_agent(lowered)
        if known:
            return known
        return self._suggest(lowered)

    def _boot(self):
        import signal

        real_signal = signal.signal
        signal.signal = lambda *args, **kwargs: None
        try:
            import Jarvis as jarvis_module

            sink = io.StringIO()
            with redirect_stdout(sink), redirect_stderr(sink):
                self.jarvis = jarvis_module.Jarvis(first_reaction=False)
            self._install_hooks()
            self._index_names()
            self.online = True
        except Exception as error:
            self.boot_error = str(error)
        finally:
            signal.signal = real_signal
            self.boot_done.set()

    def _install_hooks(self):
        engine = self
        api = self.jarvis.get_api()

        def say(text, color="", speak=True):
            engine._add(text)

        def ask(prompt="", color=""):
            return engine._input(prompt)

        api.say = say
        api.input = ask
        api.exit = lambda: (_ for _ in ()).throw(SystemExit(0))
        self.jarvis.say = say
        self.jarvis.close = api.exit
        builtins.input = lambda prompt="": engine._input(prompt)

    def _index_names(self):
        found = []

        def walk(plugin):
            name = (plugin.get_name() or "").strip().lower()
            if name and plugin.is_callable_plugin():
                found.append(name)
                for alias in plugin.alias() or []:
                    found.append(str(alias).strip().lower())
            for sub in plugin.get_plugins().values():
                walk(sub)

        for plugin in self.jarvis._plugin_manager.get_plugins().values():
            walk(plugin)
        self.names = sorted(set(item for item in found if item), key=len, reverse=True)
        self.name_set = set(self.names)
        for name in list(self.names):
            self.name_set.add(name.split()[0])

    def _add(self, text):
        plain = ANSI.sub("", str(text)).strip()
        if not plain:
            return
        with self.lock:
            self.output.append(plain)

    def _input(self, prompt=""):
        if self.worker_gen != self.generation:
            raise Cancelled()
        if prompt:
            self._add(prompt)
        with self.lock:
            self.waiting = True
        self.ready.set()
        answer = self.inbox.get()
        with self.lock:
            self.waiting = False
        if self.worker_gen != self.generation or answer is None:
            raise Cancelled()
        return str(answer).rstrip()

    def _continue_plugin(self, lowered, text):
        self.ready.clear()
        with self.lock:
            self.output = []
        if re.search(r"\b(cancel|never mind|nevermind|forget it)\b", lowered):
            self.inbox.put(None)
        else:
            self.inbox.put(text)
        return self._collect()

    def _run_and_collect(self, command):
        with self.lock:
            if self.running:
                return _pack("Still working on the last thing you said.")
            self.running = True
            self.waiting = False
            self.output = []
            self.generation += 1
            gen = self.generation
            self.current_command = command
        while not self.inbox.empty():
            try:
                self.inbox.get_nowait()
            except queue.Empty:
                break
        self.ready.clear()
        threading.Thread(
            target=self._worker,
            args=(command, gen),
            name="brong-command",
            daemon=True,
        ).start()
        return self._collect()

    def _worker(self, command, gen):
        self.worker_gen = gen
        stdout_buffer = io.StringIO()
        stderr_buffer = io.StringIO()
        try:
            with redirect_stdout(stdout_buffer), redirect_stderr(stderr_buffer):
                line = self.jarvis.precmd(command)
                self.jarvis.onecmd(line)
                self.jarvis.postcmd(False, line)
            for raw in stdout_buffer.getvalue().splitlines():
                self._add(raw)
        except Cancelled:
            self._add("Okay, cancelled.")
        except SystemExit:
            self._add("Stopped.")
        except Exception as error:
            self._add("That command failed. {0}".format(error))
        finally:
            with self.lock:
                self.running = False
                self.waiting = False
                if self.generation == gen:
                    self.ready.set()

    def _collect(self):
        if not self.ready.wait(28):
            with self.lock:
                self.generation += 1
                self.waiting = False
            return _pack("That's taking too long. Say it again.")
        with self.lock:
            lines = self._tidy(list(self.output))
            waiting = self.waiting
            title = (self.current_command or "Brong").split()[0]
        if not lines:
            return _pack("That command didn't come back with anything. Try saying it another way.")
        speech = self._speakable(lines)
        card = {
            "kind": "answer",
            "title": title.upper(),
            "temp": "",
            "detail": "\n".join(lines)[:1600],
            "meta": "Say cancel to leave this" if waiting else "",
        }
        return _pack(speech, "plugin" if waiting else None, card=card)

    def _tidy(self, lines):
        cleaned = []
        for line in lines:
            plain = ANSI.sub("", line).strip()
            if not plain or plain.startswith("Traceback") or plain.startswith('File "'):
                continue
            if "Some error occurred" in plain:
                plain = "That command hit an error."
            if plain not in cleaned:
                cleaned.append(plain)
        return cleaned

    def _speakable(self, lines):
        text = re.sub(r"\s+", " ", " ".join(lines)).strip()
        if len(text) > 480:
            return text[:480].rsplit(" ", 1)[0] + ". The rest is on screen."
        return text

    def _resolve(self, text):
        math = self._math(text)
        if math:
            return math
        for pattern, command in PHRASES:
            if re.search(pattern, text) and self._known(command):
                return command
        padded = " " + re.sub(r"[^a-z0-9 ]", " ", text) + " "
        padded = re.sub(r"\s+", " ", padded)
        for name in self.names:
            token = " " + name + " "
            if token in padded:
                rest = padded.replace(token, " ", 1)
                rest = FILLER.sub(" ", rest)
                rest = re.sub(r"\s+", " ", rest).strip()
                if rest in {"me", "now", "again"}:
                    rest = ""
                return (name + " " + rest).strip()
        return None

    def _known(self, command):
        head = command.split()[0]
        return command in self.name_set or head in self.name_set

    def _blocked(self, command):
        head = command.split()[0]
        return head in BLOCKED or command in BLOCKED

    def _math(self, text):
        match = re.search(r"(?:what(?:'s| is)|calculate|calc|compute)\s+(.+)$", text)
        if not match or not re.search(r"\d", match.group(1)):
            return None
        expr = match.group(1)
        expr = (
            expr.replace("multiplied by", "*")
            .replace("divided by", "/")
            .replace("times", "*")
            .replace("plus", "+")
            .replace("minus", "-")
        )
        expr = re.sub(r"[^0-9xX\+\-\*/\^\.\(\)=]", "", expr)
        if not expr or not re.search(r"\d", expr):
            return None
        return "calculate " + expr

    def _help(self):
        examples = [
            "tell me a joke",
            "I'm bored",
            "check the forecast",
            "calculate 6 times 7",
            "cat fact",
            "play hangman",
            "check my email",
        ]
        live = [name for name in examples if True]
        say = (
            "I'm listening. You can say {0}. "
            "I have {1} commands loaded, and if I ask a question, just answer out loud."
        ).format(", ".join(live), len(self.names))
        shown = [name for name in reversed(self.names) if " " not in name][:40]
        return _pack(
            say,
            card={
                "kind": "answer",
                "title": "BRONG",
                "temp": str(len(self.names)),
                "detail": "Say any of these, or describe what you want.\n" + ", ".join(shown),
                "meta": "Click the core if the mic is off",
            },
        )

    def _conversation_agent(self, text):
        """Answers Brong itself: name, greeting, and small talk."""
        if re.search(
            r"\b(what is your name|what's your name|whats your name|who are you|"
            r"what are you|your name|what do i call you)\b",
            text,
        ):
            say = "My name is Brong. I am here to assist you."
            return _pack(say, card=_agent_card("BRONG", say))
        if re.search(r"\bhow are you\b", text):
            say = "I'm here and listening. Tell me what you want done."
            return _pack(say, card=_agent_card("BRONG", say))
        if re.search(r"^(hi|hello|hey|good morning|good evening|good afternoon)\b", text):
            say = "Hello. How can I help you today? I am here to assist you."
            return _pack(say, card=_agent_card("BRONG", say))
        if re.search(r"\b(thank you|thanks)\b", text):
            say = "You're welcome."
            return _pack(say, card=_agent_card("BRONG", say))
        return None

    def _knowledge_agent(self, text):
        """Looks up a straight answer when the words are a question, not a task."""
        if not re.search(r"\b(who|what|when|where|why|how|tell me about|define|explain)\b", text):
            return None
        if re.search(r"\b(you|your|brong)\b", text):
            say = "I'm Brong. Ask me for a forecast, a joke, a fact, your email, or something you want looked up."
            return _pack(say, card=_agent_card("BRONG", say))
        topic = re.sub(
            r"^(please |can you |could you |would you |tell me |do you know |"
            r"what is |what's |whats |who is |who was |where is |where are |"
            r"when is |when was |why is |how do |how does |define |explain )",
            "",
            text,
        )
        topic = topic.strip(" ?.")
        if len(topic) < 2:
            return None
        try:
            found = _wiki(topic)
        except Exception:
            found = None
        if not found:
            return None
        title, blurb = found
        return _pack(blurb, card=_agent_card("KNOWLEDGE", blurb))

    def _suggest(self, text):
        match = difflib.get_close_matches(text, self.names, n=1, cutoff=0.72)
        if not match:
            words = [word for word in text.split() if len(word) > 3]
            for word in words:
                match = difflib.get_close_matches(word, self.names, n=1, cutoff=0.8)
                if match:
                    break
        if match and not self._blocked(match[0]):
            self.suggestion = match[0]
            return _pack(
                "I don't have that exactly. Did you mean {0}?".format(match[0]),
                "confirm",
                card={
                    "kind": "answer",
                    "title": "DID YOU MEAN",
                    "temp": "",
                    "detail": match[0],
                    "meta": "Say yes or no",
                },
            )
        return _pack(
            "I heard you. Say it as a task, like the forecast, a joke, a fact, your email, or a question I can look up.",
            card=_agent_card("BRONG", text),
        )
