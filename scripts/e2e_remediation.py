"""Live lab checks. API credentials come from the shell launcher, never arguments.

Requires AWS CLI access to SSM on xoc-test. Creates isolated harmless fixtures.
Generated plans are displayed and require interactive approval before execution.
Evidence is private and excludes callback tokens. No application dependencies.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import time
import urllib.error
import urllib.request
import uuid


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--instance-id", default="i-0c9cc83ad92fcb7a8")
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--wait-seconds", type=int, default=600)
    parser.add_argument("--min-plans", type=int, default=3)
    parser.add_argument("--prepare-demo", action="store_true",
                        help="Crear cinco tickets y dejarlos PREAPROBADO; nunca aprobar ni ejecutar")
    args = parser.parse_args()
    os.umask(0o077)
    run = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
    root = "/opt/xoc-test/incident/e2e-remediation/" + run
    output = Path("local-output") / ("e2e-remediation-" + run)
    output.mkdir(parents=True)
    results = []
    base = os.environ.get("API_BASE", "https://xvwg3cvl6b.execute-api.us-east-1.amazonaws.com").rstrip("/")

    def check(ok, message):
        results.append({"pass": bool(ok), "message": message})
        print(("[PASS] " if ok else "[FAIL] ") + message, flush=True)

    def clean(value):
        if isinstance(value, dict):
            return {k: clean(v) for k, v in value.items()
                    if "token" not in k.lower() and "password" not in k.lower()}
        if isinstance(value, list):
            return [clean(v) for v in value]
        return value

    def save(name, value):
        (output / (name + ".json")).write_text(
            json.dumps(clean(value), ensure_ascii=False, indent=2), encoding="utf-8")

    def api(method, path, token=None, payload=None):
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = "Bearer " + token
        req = urllib.request.Request(base + path, headers=headers, method=method,
                                     data=json.dumps(payload).encode() if payload is not None else None)
        try:
            with urllib.request.urlopen(req, timeout=60) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode()
            try:
                body = json.loads(raw)
            except ValueError:
                body = {"error": raw[:500]}
            return exc.code, body

    def login(prefix):
        email = os.environ.get(prefix + "_EMAIL")
        password = os.environ.get(prefix + "_PASSWORD")
        if not email or not password:
            raise RuntimeError("Faltan credenciales " + prefix)
        status, body = api("POST", "/auth/login", payload={"email": email, "password": password})
        if status != 200 or not body.get("access_token"):
            raise RuntimeError("Login " + prefix + " fallo: HTTP " + str(status))
        return body["access_token"]

    def aws(*parts):
        proc = subprocess.run(["aws", "--region", args.region, *parts, "--output", "json"],
                              capture_output=True, text=True, timeout=45)
        if proc.returncode:
            raise RuntimeError("AWS SSM: " + proc.stderr.strip()[:800])
        return json.loads(proc.stdout)

    def remote(command):
        sent = aws("ssm", "send-command", "--instance-ids", args.instance_id,
                   "--document-name", "AWS-RunShellScript", "--parameters",
                   json.dumps({"commands": ["set -eu\n" + command], "executionTimeout": ["60"]}))
        command_id = sent["Command"]["CommandId"]
        deadline = time.monotonic() + 100
        while time.monotonic() < deadline:
            inv = aws("ssm", "list-command-invocations", "--command-id", command_id, "--details")
            entries = inv.get("CommandInvocations", [])
            if entries and entries[0]["Status"] not in ("Pending", "InProgress", "Delayed"):
                entry = entries[0]
                text = "\n".join(p.get("Output", "") for p in entry.get("CommandPlugins", []))
                if entry["Status"] != "Success":
                    raise RuntimeError("SSM " + entry["Status"] + ": " + text[:1000])
                return text.strip()
            time.sleep(3)
        raise RuntimeError("Timeout SSM; no se continuara con aprobaciones")

    def ticket(token, tid):
        status, doc = api("GET", "/tickets/" + tid, token)
        if status != 200:
            raise RuntimeError("GET ticket: HTTP " + str(status))
        return doc

    def poll(token, tid, wanted):
        deadline = time.monotonic() + args.wait_seconds
        while time.monotonic() < deadline:
            doc = ticket(token, tid)
            state = doc.get("status")
            if state in wanted or state in {"RESUELTO", "FALLIDO", "DERIVADO", "RECHAZADO"}:
                return doc
            time.sleep(5)
        raise RuntimeError("Timeout ticket " + tid + "; revisar workflow antes de repetir")

    def prepare_demo(admin):
        scenarios = [
            ("01-lectura", "Inspeccion sin cambios (USER)", False),
            ("02-recomendado", "Eliminacion con plan recomendado (ADMIN)", True),
            ("03-alternativo", "Respaldo y eliminacion con plan alternativo (ADMIN)", True),
            ("04-personalizado", "Plan personalizado con preview (ADMIN)", True),
            ("05-rechazo", "Rechazar remediacion y conservar archivo (ADMIN)", True),
        ]
        manifest = {"run": run, "root": root, "instance": args.instance_id, "tickets": []}
        guide = [f"# Demo XOC {run}", "", f"Directorio en xoc-test: `{root}`", "",
                 "Tickets creados sin aprobar ni ejecutar. Usar USER para lectura y bloqueo por riesgo; ADMIN para remediar.", ""]
        q = shlex.quote
        for mode, title, changes in scenarios:
            directory = root + "/" + mode
            target, keeper = directory + "/objetivo.txt", directory + "/testigo.txt"
            original = f"XOC DEMO archivo inofensivo {run} {mode}\n"
            preserved = f"XOC DEMO testigo conservar {run} {mode}\n"
            remote("mkdir -p " + q(directory) + "\nchmod 0777 " + q(directory)
                   + "\nprintf %s " + q(original) + " > " + q(target)
                   + "\nprintf %s " + q(preserved) + " > " + q(keeper)
                   + "\nchmod 0666 " + q(target) + " " + q(keeper)
                   + "\ncd /opt/victor\ndocker compose exec -T executor sh -c "
                   + q("test -f " + q(target) + " && test -f " + q(keeper)))
            if changes:
                goal = (f"Retirar exclusivamente {target} de su ubicacion original. Genera tres planes "
                        "diferentes y ejecutables: eliminacion directa; copia de respaldo y eliminacion; "
                        "traslado a cuarentena. Respaldos y cuarentena deben estar dentro del directorio "
                        f"autorizado {directory}. Inspecciona el archivo primero y verifica ausencia final, "
                        "devolviendo error si no se cumple el objetivo. No eliminar directorios completos.")
            else:
                goal = (f"Inspeccionar {target}: verificar existencia, mostrar contenido y calcular SHA-256. "
                        "SOLO LECTURA, riesgo basic. No crear, mover, eliminar ni modificar archivos. "
                        "Una unica estrategia de lectura es suficiente.")
            description = (f"Demo autorizada en xoc-test, IP privada 10.20.0.97. {goal} "
                           f"Conservar {keeper} y todos los otros directorios y archivos sin cambios. "
                           "Los planes se presentaran para aprobacion humana; no ejecutar durante la generacion.")
            code, created = api("POST", "/tickets", admin, {
                "subject": f"DEMO {run} | {title}", "description": description,
                "priority": "high", "metadata": {"source": "demo-preapproved", "run": run, "scenario": mode}})
            if code != 201:
                raise RuntimeError(f"Crear ticket {mode}: HTTP {code}; revisar manifest antes de repetir")
            tid = created["ticket"]["id"]
            entry = {"scenario": mode, "ticket_id": tid, "title": title, "target": target,
                     "keeper": keeper, "target_sha256": hashlib.sha256(original.encode()).hexdigest(),
                     "keeper_sha256": hashlib.sha256(preserved.encode()).hexdigest(),
                     "required_options": args.min_plans if changes else 1}
            manifest["tickets"].append(entry)
            save("demo-manifest", manifest)
            print(f"[CREADO] {mode}: {tid}", flush=True)
            guide += [f"## {title}", f"Ticket: `{tid}`", f"Archivo: `{target}`", ""]
            if mode == "04-personalizado":
                guide += ["Texto para pegar en plan personalizado y previsualizar:", "", "```text",
                          f"Inspecciona {target} y calcula su SHA-256. Crea {directory}/cuarentena-demo. "
                          f"Mueve solo {target} a {directory}/cuarentena-demo/objetivo.txt. "
                          "Comprueba que el original ya no existe y el destino conserva el hash. "
                          f"No modificar {keeper} ni ninguna otra ruta. Las verificaciones deben fallar "
                          "con codigo distinto de cero si no se cumple el objetivo.", "```", ""]
        (output / "demo-guide.md").write_text("\n".join(guide), encoding="utf-8")
        for entry in manifest["tickets"]:
            doc = poll(admin, entry["ticket_id"], {"PREAPROBADO"})
            save(entry["scenario"] + "-prepared", doc)
            entry["status"] = doc.get("status")
            options = (doc.get("pending_decision") or {}).get("options") or []
            entry["options"] = len(options)
            check(doc.get("status") == "PREAPROBADO", entry["scenario"] + ": " + str(doc.get("status")))
            check(len(options) >= entry["required_options"],
                  f"{entry['scenario']}: {len(options)} planes (minimo {entry['required_options']})")
            save("demo-manifest", manifest)
        # Verify preparation had no remediation side effects in the target server.
        verify = "import pathlib,hashlib,json\nitems=" + repr(manifest["tickets"]) + "\n"
        verify += ("out=[]\nfor item in items:\n"
                   " p=pathlib.Path(item['target']); k=pathlib.Path(item['keeper'])\n"
                   " out.append({'scenario':item['scenario'], 'target_ok':p.is_file() and hashlib.sha256(p.read_bytes()).hexdigest()==item['target_sha256'], "
                   "'keeper_ok':k.is_file() and hashlib.sha256(k.read_bytes()).hexdigest()==item['keeper_sha256']})\n"
                   "print(json.dumps(out))\n")
        disk = json.loads(remote("python3 - <<'PY'\n" + verify + "PY"))
        save("demo-preparation-disk", disk)
        for item in disk:
            check(item["target_ok"] and item["keeper_ok"], item["scenario"] + ": archivos preparados intactos")
        print(f"\nAbre XOC Test Lab y busca: DEMO {run}\nGuia y texto custom: {output / 'demo-guide.md'}", flush=True)

    try:
        admin = login("ADMIN")
        user = None if args.prepare_demo else login("USER")
        # SSM must work before creating tickets or approving anything.
        remote("test -d /opt/victor && cd /opt/victor && docker compose exec -T executor sh -c 'test -d /opt/xoc-test/incident'")
        check(True, "SSM y montaje del executor accesibles")
        if args.prepare_demo:
            prepare_demo(admin)
            return 1 if any(not r["pass"] for r in results) else 0
        fixture = "XOC E2E harmless fixture " + run + "\n"
        keeper = "XOC E2E preserve " + run + "\n"
        for mode in ("recommended", "alternative", "custom", "reject"):
            directory = root + "/" + mode
            target = directory + "/delete-me.txt"
            keep = directory + "/keep-me.txt"
            q = shlex.quote
            remote("mkdir -p " + q(directory) + "\nchmod 0777 " + q(directory)
                   + "\nprintf %s " + q(fixture) + " > " + q(target)
                   + "\nprintf %s " + q(keeper) + " > " + q(keep)
                   + "\nchmod 0666 " + q(target) + " " + q(keep))
            description = (
                f"Prueba autorizada en xoc-test 10.20.0.97. Archivo inofensivo: {target}. "
                f"Objetivo: eliminarlo de su ubicacion original. Preservar {keep} sin cambios. "
                f"Genera al menos {args.min_plans} planes alternativos ejecutables: eliminacion directa; "
                "respaldo y eliminacion; traslado a cuarentena. Cada plan debe verificar existencia inicial "
                "y ausencia final, devolviendo error si no cumple el objetivo. "
                f"Todos los cambios deben limitarse a {directory}. No modificar servicios, "
                "permisos del sistema ni eliminar directorios completos.")
            status, created = api("POST", "/tickets", admin, {
                "subject": f"E2E {run} {mode}", "description": description,
                "priority": "high", "metadata": {"source": "e2e-remediation", "run": run}})
            if status != 201:
                raise RuntimeError("Crear ticket: HTTP " + str(status))
            tid = created["ticket"]["id"]
            print(f"\n[{mode}] Ticket {tid}", flush=True)
            doc = poll(admin, tid, {"PREAPROBADO"})
            save(mode + "-before", doc)
            if doc.get("status") != "PREAPROBADO":
                check(False, mode + " no llego a PREAPROBADO: " + str(doc.get("status")))
                continue
            pending = doc.get("pending_decision") or {}
            options = pending.get("options") or []
            check(len(options) >= args.min_plans, f"{mode}: {len(options)} opciones (minimo {args.min_plans})")
            check(len(doc.get("action_plans") or []) == len(options), mode + ": planes y opciones coinciden")
            if mode == "custom":
                command = f"test -f {q(target)} && rm -- {q(target)} && test ! -e {q(target)}"
                payload = {"custom_plan_steps": [{"order": 1, "action": "shell", "command": command,
                            "description": "Eliminar solo fixture y verificar ausencia", "risk_level": "risky"}]}
                code, preview = api("PATCH", f"/tickets/{tid}/decision/custom/preview", admin, payload)
                save(mode + "-preview", preview)
                check(code == 200 and preview.get("can_execute") is True, "ADMIN preview custom autorizado")
                check(ticket(admin, tid).get("status") == "PREAPROBADO", "Preview conserva PREAPROBADO")
                code, denied = api("PATCH", f"/tickets/{tid}/decision/custom", user, payload)
                check(code == 403, "USER no puede ejecutar eliminacion: HTTP " + str(code))
                if code != 403:
                    raise RuntimeError("Gobernanza inesperada; se detiene para revisar")
                action, body = "decision/custom", payload
            elif mode == "reject":
                action, body = "reject", None
            else:
                recommended = pending.get("recommended_option_id")
                selected = next((o for o in options if o.get("option_id") == recommended), None)
                if mode == "alternative":
                    selected = next((o for o in options if o.get("option_id") != recommended), None)
                if len(options) < args.min_plans or not selected:
                    check(False, mode + ": no se ejecuta porque faltan alternativas")
                    continue
                print(json.dumps(selected, ensure_ascii=False, indent=2), flush=True)
                answer = input(f"Revisa que SOLO cambie {directory}. Escribe EJECUTAR para aprobar: ")
                if answer != "EJECUTAR":
                    check(False, mode + ": omitido por el operador; ticket queda PREAPROBADO")
                    continue
                save(mode + "-selected", selected)
                action = "approve" if mode == "recommended" else "decision/select"
                body = None if mode == "recommended" else {"selected_option_id": selected["option_id"]}
            code, response = api("PATCH", f"/tickets/{tid}/{action}", admin, body)
            save(mode + "-approval", {"http_status": code, "response": response})
            check(code == 200, mode + ": decision HTTP " + str(code))
            if code != 200:
                continue
            expected = "RECHAZADO" if mode == "reject" else "RESUELTO"
            final = poll(admin, tid, {expected})
            save(mode + "-after", final)
            check(final.get("status") == expected, mode + ": estado " + str(final.get("status")))
            evidence = remote("python3 - <<'PY'\nimport pathlib,hashlib,json\n"
                              + f"p=pathlib.Path({target!r}); k=pathlib.Path({keep!r})\n"
                              + "print(json.dumps({'target_exists':p.exists(), 'keeper_sha256':hashlib.sha256(k.read_bytes()).hexdigest() if k.is_file() else None}))\nPY")
            disk = json.loads(evidence)
            save(mode + "-disk", disk)
            check(disk["target_exists"] == (mode == "reject"), mode + ": existencia real del archivo correcta")
            check(disk["keeper_sha256"] == hashlib.sha256(keeper.encode()).hexdigest(), mode + ": archivo testigo intacto")
            if mode != "reject":
                logs = final.get("execution_logs") or {}
                check(bool(logs.get("timeline")), mode + ": timeline registrado")
                check(final.get("execution_status") == "EXECUTED", mode + ": execution_status EXECUTED")
            # Case registration follows the ticket update asynchronously.
            case = {}
            code = 404
            for _ in range(12):
                code, case = api("GET", f"/cases/ticket/{tid}", admin)
                if code == 200:
                    break
                if code != 404:
                    break
                time.sleep(5)
            save(mode + "-case", {"http_status": code, "case": case})
            check(code == 200, mode + ": caso de automatizacion registrado")
            if code == 200 and mode != "reject":
                actual_plan = final.get("action_plan") or {}
                recorded_plan = case.get("plan_used") or {}
                check(recorded_plan.get("steps") == actual_plan.get("steps"),
                      mode + ": caso guarda el plan realmente ejecutado")
    except (Exception, KeyboardInterrupt) as exc:
        check(False, str(exc) or "Interrumpido; revisar tickets antes de repetir")
    finally:
        save("summary", {"run": run, "instance": args.instance_id, "root": root, "results": results})
        passed = sum(r["pass"] for r in results)
        print(f"\nResultado: {passed} OK, {len(results)-passed} fallos. Evidencia: {output}")
        if args.prepare_demo:
            print("Modo preparacion: no se enviaron aprobaciones, rechazos ni planes a ejecutar.")
        else:
            print("No se borran tickets ni evidencias. Esta suite no prueba timeout de 7 dias ni fallos inducidos.")
    return 1 if any(not r["pass"] for r in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
