from flask import Flask, request, jsonify, Response
import json, os, re, time, threading, datetime, requests
from functools import wraps

app = Flask(__name__)
DATA_FILE = "data/week.json"
SCHOOL_MENU_FILE = "data/school_menu.json"
SCHOOL_MENU_DEBUG_FILE = "data/school_menu_debug.json"

# Matilda URL (din)
MATILDA_URL = "https://menu.matildaplatform.com/en/meals/week/68f9fc37bf545da84ec60b23_forskola-och-skola"

NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
    re.DOTALL,
)

# ===== MULTI-USER BASIC AUTH =====
USERS = {
    "person1": "password",
    "person2": "password"
}

def check_auth(username, password):
    return username in USERS and USERS[username] == password

def authenticate():
    return Response(
        "Authentication required",
        401,
        {"WWW-Authenticate": 'Basic realm="Login Required"'}
    )

def requires_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth = request.authorization
        if not auth or not check_auth(auth.username, auth.password):
            return authenticate()
        return f(*args, **kwargs)
    return decorated

# ===== WEEK MENU DATA =====
# Internal/storage keys stay in English so they keep matching the existing
# week.json / school_menu.json files on disk. Swedish labels are applied
# only in the UI layer (see DAY_LABELS_SV in the page's JS below).
DAYS = ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"]

def load_data():
    if not os.path.exists(DATA_FILE):
        return {}
    try:
        with open(DATA_FILE, "r") as f:
            return json.load(f)
    except:
        return {}

def save_data(data):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=2)

# ===== SCHOOL MENU SCRAPER =====
def scrape_school_menu():
    """
    Matilda's page is a Next.js app that server-renders the whole week's
    menu into a <script id="__NEXT_DATA__"> JSON blob (props.pageProps.meals),
    so we pull that straight out with a plain GET - no browser needed.

    Never overwrites SCHOOL_MENU_FILE with an all-empty result - a scrape
    that finds 0 dishes is treated as a failure and the existing file is
    left untouched. The raw parsed payload is also stashed in
    data/school_menu_debug.json to make future format changes easy to
    diagnose.
    """
    days_data = {d: [] for d in DAYS}
    try:
        r = requests.get(MATILDA_URL, timeout=15)
        if r.status_code != 200:
            print("Matilda scrape failed: status", r.status_code)
            return False

        match = NEXT_DATA_RE.search(r.text)
        if not match:
            print("Matilda scrape failed: __NEXT_DATA__ script tag not found "
                  "(page structure may have changed)")
            return False

        next_data = json.loads(match.group(1))

        try:
            os.makedirs("data", exist_ok=True)
            with open(SCHOOL_MENU_DEBUG_FILE, "w") as f:
                json.dump(next_data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print("Could not write school menu debug file:", e)

        meals = next_data.get("props", {}).get("pageProps", {}).get("meals", [])

        for meal in meals:
            date_str = meal.get("date")
            if not date_str:
                continue
            try:
                d = datetime.datetime.fromisoformat(date_str)
            except ValueError:
                continue
            day_name = DAYS[d.weekday()]
            for course in meal.get("courses", []):
                name = (course.get("name") or "").strip()
                if name and name not in days_data[day_name]:
                    days_data[day_name].append(name)

        total_dishes = sum(len(v) for v in days_data.values())
        if total_dishes == 0:
            print(
                "Matilda scrape: parsed __NEXT_DATA__ successfully but found "
                "0 dishes, keeping existing school_menu.json. Check "
                "data/school_menu_debug.json for the raw payload."
            )
            return False

        with open(SCHOOL_MENU_FILE, "w") as f:
            json.dump(days_data, f, indent=2, ensure_ascii=False)
        print("School menu updated:", days_data)
        return True

    except Exception as e:
        print("School menu scrape failed:", e)
        return False

def scheduler_thread():
    """
    Runs every minute. At Monday 06:00 it scrapes school menu once (and
    keeps retrying every minute until it succeeds or the day changes, since
    a failed scrape no longer touches the file's mtime).
    """
    while True:
        now = datetime.datetime.now()
        if now.weekday() == 0 and now.hour >= 6:  # Monday
            try:
                ts = os.path.getmtime(SCHOOL_MENU_FILE)
                last = datetime.datetime.fromtimestamp(ts)
                if last.date() != now.date():
                    scrape_school_menu()
            except:
                scrape_school_menu()
        time.sleep(60)

# Start scheduler thread
threading.Thread(target=scheduler_thread, daemon=True).start()

# ===== LOAD SCHOOL MENU =====
def load_school_menu():
    if not os.path.exists(SCHOOL_MENU_FILE):
        return {d: [] for d in DAYS}
    try:
        with open(SCHOOL_MENU_FILE, "r") as f:
            return json.load(f)
    except:
        return {d: [] for d in DAYS}

# ===== API ROUTES =====
@app.route("/api/week", methods=["GET"])
@requires_auth
def get_week():
    return jsonify(load_data())

@app.route("/api/week", methods=["POST"])
@requires_auth
def update_week():
    data = request.json
    save_data(data)
    return {"status": "ok"}

@app.route("/api/school_menu/reload", methods=["POST"])
@requires_auth
def reload_school_menu():
    ok = scrape_school_menu()
    menu = load_school_menu()
    dish_count = sum(len(v) for v in menu.values())
    if ok:
        return jsonify({"status": "ok", "dish_count": dish_count})
    return jsonify({
        "status": "error",
        "error": "Scrape found no data - check container logs and "
                 "data/school_menu_debug.json",
        "dish_count": dish_count,
    }), 500

# ===== UI =====
@app.route("/")
@requires_auth
def index():
    html = """
<!DOCTYPE html>
<html>
<head>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  body { font-family: sans-serif; padding: 20px; }
  input { width: 100%; padding: 10px; margin: 5px 0; font-size: 16px; }
  button { padding: 12px; font-size: 16px; margin: 5px; }
  h3 { margin-top: 20px; }
  #buttons { margin-bottom: 20px; }
  .recipe-btn { margin-top: 5px; display: inline-block; padding: 8px; background: #007bff; color: white; text-decoration: none; border-radius: 6px; }
  .school-list { margin: 6px 0 10px 12px; color: #333; font-size: 15px; }
  .school-title { font-weight: 600; color: #222; margin-bottom: 4px; }
  #reloadBtn { background: #28a745; color: white; border: none; border-radius: 6px; }
  #reloadBtn:disabled { background: #999; }
</style>
</head>
<body>
<h2>Veckomeny</h2>
<div id="buttons">
  <button onclick="showToday()">Idag</button>
  <button onclick="showWeek()">Vecka</button>
  <button onclick="enableEdit()">Editera</button>
  <button id="reloadBtn" onclick="reloadSchoolMenu()">Ladda om skolmat</button>
</div>
<div id="form"></div>
<button onclick="save()">Spara</button>
<script>
  // Interna/lagrade nycklar (matchar week.json / school_menu.json på disk)
  var days = ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"];
  // Svenska etiketter som visas i gränssnittet
  var dayLabels = {
    "Monday": "Måndag",
    "Tuesday": "Tisdag",
    "Wednesday": "Onsdag",
    "Thursday": "Torsdag",
    "Friday": "Fredag",
    "Saturday": "Lördag",
    "Sunday": "Söndag"
  };
  var data = {};
  var schoolMenu = {};
  var mode = "week";
  var editMode = false;
  function fetchWeek() {
    return fetch('/api/week').then(r => r.json());
  }
  function fetchSchoolMenu() {
    return fetch('/school_menu').then(r => r.json());
  }
  function init() {
    Promise.all([fetchWeek(), fetchSchoolMenu()]).then(function(res){
      data = res[0];
      schoolMenu = res[1];
      render();
    });
  }
  function getTodayName() {
    var jsDay = new Date().getDay();
    var map = {0:"Sunday",1:"Monday",2:"Tuesday",3:"Wednesday",4:"Thursday",5:"Friday",6:"Saturday"};
    return map[jsDay];
  }
  function enableEdit() {
    editMode = true;
    render();
  }
  function reloadSchoolMenu() {
    var btn = document.getElementById('reloadBtn');
    btn.disabled = true;
    btn.innerText = "Laddar om...";
    fetch('/api/school_menu/reload', { method: 'POST' })
      .then(r => r.json().then(body => ({ ok: r.ok, body: body })))
      .then(function(res) {
        if (res.ok) {
          alert("Skolmatsedeln laddades om (" + res.body.dish_count + " rätter).");
        } else {
          alert("Omladdning misslyckades: " + res.body.error);
        }
        return fetchSchoolMenu();
      })
      .then(function(sm) {
        schoolMenu = sm;
        render();
      })
      .catch(function(err) {
        alert("Omladdning misslyckades: " + err);
      })
      .finally(function() {
        btn.disabled = false;
        btn.innerText = "Ladda om skolmat";
      });
  }
  function renderSchool(day) {
    var items = schoolMenu[day] || [];
    if (items.length === 0) return "";
    var html = "<div class='school-title'>Skollunch:</div><ul class='school-list'>";
    for (var i = 0; i < items.length; i++) {
      html += "<li>" + items[i] + "</li>";
    }
    html += "</ul>";
    return html;
  }
  function renderDay(day) {
    var d = data[day] || {};
    var lunch = d.lunch || "";
    var dinner = d.dinner || "";
    var lunchRecipe = d.lunch_recipe || "";
    var dinnerRecipe = d.dinner_recipe || "";
    var html = "<h3>" + dayLabels[day] + "</h3>";
    html += renderSchool(day);
    html += "Lunch: <input id='" + day + "_lunch' value='" + lunch + "'>";
    if (editMode) {
      html += "Lunch recept: <input id='" + day + "_lunch_recipe' value='" + lunchRecipe + "'>";
    } else {
      if (lunchRecipe.length > 0) {
        html += "<br><a class='recipe-btn' href='" + lunchRecipe + "' target='_blank'>Öppna recept</a>";
      }
    }
    html += "<br><br>";
    html += "Middag: <input id='" + day + "_dinner' value='" + dinner + "'>";
    if (editMode) {
      html += "Middag recept: <input id='" + day + "_dinner_recipe' value='" + dinnerRecipe + "'>";
    } else {
      if (dinnerRecipe.length > 0) {
        html += "<br><a class='recipe-btn' href='" + dinnerRecipe + "' target='_blank'>Öppna recept</a>";
      }
    }
    return html;
  }
  function render() {
    var html = "";
    if (mode === "today") {
      html += renderDay(getTodayName());
    } else {
      for (var i = 0; i < days.length; i++) {
        html += renderDay(days[i]);
      }
    }
    document.getElementById("form").innerHTML = html;
  }
  function showToday() { mode = "today"; render(); }
  function showWeek() { mode = "week"; render(); }
  function save() {
    for (var i = 0; i < days.length; i++) {
      var day = days[i];
      var d = data[day] || {};
      d.lunch = document.getElementById(day + "_lunch").value;
      d.dinner = document.getElementById(day + "_dinner").value;
      if (editMode) {
        d.lunch_recipe = document.getElementById(day + "_lunch_recipe").value;
        d.dinner_recipe = document.getElementById(day + "_dinner_recipe").value;
      }
      data[day] = d;
    }
    editMode = false;
    fetch('/api/week', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(data)
    }).then(() => { alert("Sparat!"); render(); });
  }
  init();
</script>
</body>
</html>
    """
    return Response(html, mimetype="text/html")

# ===== SCHOOL MENU ENDPOINT =====
@app.route("/school_menu")
@requires_auth
def school_menu():
    return jsonify(load_school_menu())

# ===== MAIN =====
if __name__ == "__main__":
    if not os.path.exists("data"):
        os.makedirs("data")
    if not os.path.exists(DATA_FILE):
        save_data({})
    if not os.path.exists(SCHOOL_MENU_FILE):
        scrape_school_menu()
    app.run(host="0.0.0.0", port=8080)
