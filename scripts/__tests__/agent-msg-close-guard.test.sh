#!/usr/bin/env bash
# --close/--result: does the guard refuse a PREDICTED message id, and can it also say yes?
#
# What it guards: on 2026-09-10/11 four inter-agent rows carried a "Reszletek: msg N" that was
# off by one, from two different agents, because the result text was written in the SAME command
# as the send -- so the number was a prediction, not a measurement. In all four cases the id in
# between belonged to the other agent's receipt.
#
# WHY EVERY CASE POINTS AT A DEAD PORT. The guard cases exit before any send, but the
# NEGATIVE-CONTROL cases (the ones that must get PAST the guard) would otherwise post a real
# row -- and the first version of this file did exactly that: three rows to marveen, three
# inbox wake-ups, while its own header claimed "posts nothing". So the controls run against a
# closed port: the guard still runs, the send then fails, and no row is created anywhere.
# The two outcomes stay distinguishable because we assert the MESSAGE, not just the exit code.
set -uo pipefail
BASE="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
S="$BASE/scripts/agent-msg.sh"
DEAD_PORT=1          # nothing listens here, and binding it would need root
fails=0

# expect_guard: the guard must refuse, exit 2, and SAY why. No send may happen.
expect_guard() {
  local name="$1"; shift
  local out; out="$(MARVEEN_WEB_PORT=$DEAD_PORT bash "$S" "$@" 2>&1)"; local got=$?
  if [ "$got" = 2 ] && printf '%s' "$out" | grep -q "nincs benne {id}"; then
    echo "OK   $name"
  else
    echo "FAIL $name -- exit $got (vart 2), es a kapu indoklasa hianyzik"; echo "     $out" | head -3
    fails=$((fails+1))
  fi
}

# expect_past_guard: the guard must let it through. It then dies on the dead port, which is a
# DIFFERENT failure with a DIFFERENT message -- that difference is the whole assertion.
expect_past_guard() {
  local name="$1"; shift
  local out; out="$(MARVEEN_WEB_PORT=$DEAD_PORT bash "$S" "$@" 2>&1)"; local got=$?
  if [ "$got" = 1 ] && printf '%s' "$out" | grep -q "^FAIL from=" \
     && ! printf '%s' "$out" | grep -q "nincs benne {id}"; then
    echo "OK   $name"
  else
    echo "FAIL $name -- exit $got (vart 1), vagy a kapu fogta meg tevesen"; echo "     $out" | head -3
    fails=$((fails+1))
  fi
}

expect_arg_error() {
  local name="$1" needle="$2"; shift 2
  local out; out="$(MARVEEN_WEB_PORT=$DEAD_PORT bash "$S" "$@" 2>&1)"; local got=$?
  if [ "$got" = 2 ] && printf '%s' "$out" | grep -q "$needle"; then echo "OK   $name"
  else echo "FAIL $name -- exit $got, out: $out"; fails=$((fails+1)); fi
}

# --- 1. the point: a named message id without {id} is refused ----------------
expect_guard "azonosito {id} nelkul"      --close 9999 --result "Kesz. Reszletek: msg 4510" marveen anorak "x"
expect_guard "msg_id: alak"               --close 9999 --result "lasd msg_id:4510" marveen anorak "x"
expect_guard "nagybetus MSG"              --close 9999 --result "Reszletek: MSG 4510" marveen anorak "x"

# --- 2. negative control: it must also be able to say yes -------------------
expect_past_guard "{id}-vel atmegy"       --close 9999 --result "Reszletek: msg {id}" marveen marveen "x"
expect_past_guard "azonosito nelkul atmegy" --close 9999 --result "Kesz, reszletek kulon uzenetben." marveen marveen "x"
expect_past_guard "sima szam nem akad el" --close 9999 --result "4837 karakter jott vissza" marveen marveen "x"

# --- 2b. MENTION vs USE: {{id}} must survive as a literal {id} ---------------
# The first live use of the flag was its OWN announcement, and that is exactly what broke:
# the substitution was global, so a sentence explaining the placeholder had the placeholder
# replaced inside it ("a 4507 helyere a visszakapott id kerul"). Anorak measured it on the
# receipt. These cases run the substitution alone -- the same three lines the script runs --
# because the rendering happens only after a successful send, which this file never does.
render_check() {
  local name="$1" src="$2" want="$3"
  local got; got="$(SRC="$src" ID=4520 python3 -c '
import os
SENT = "\x00ID\x00"
t = os.environ["SRC"].replace("{{id}}", SENT).replace("{id}", os.environ["ID"]).replace(SENT, "{id}")
print(t)')"
  if [ "$got" = "$want" ]; then echo "OK   $name"
  else echo "FAIL $name -- kapott: $got | vart: $want"; fails=$((fails+1)); fi
}
render_check "pointer cserelodik"        'Reszletek: msg {id}'  'Reszletek: msg 4520'
render_check "emlites literalis marad"   'a {{id}} helyere kerul' 'a {id} helyere kerul'
render_check "a ketto egy mondatban is"  'a {{id}} helyorzo, mutato: msg {id}' 'a {id} helyorzo, mutato: msg 4520'
# es a kapu vak pontja: a {{id}} tartalmazza a {id}-t, de EMLITES, nem pointer
expect_guard "csak emlites + kemeny szam" --close 9999 --result "msg 4510, es a {{id}} a helyorzo" marveen anorak "x"

# Each extra pair peels one level -- so a sentence CAN name the escape itself. Anorak hit the
# wall the two-level rule leaves (2026-09-11 00:5x): the doubled form cannot be mentioned with
# the doubled form. Not a special case for three; it falls out of the leftmost-first swap.
render_check "haromszoros -> duplazott"  'a {{{id}}} alak'   'a {{id}} alak'
render_check "negyszeres -> haromszoros" 'a {{{{id}}}} alak' 'a {{{id}}} alak'
# HAROM alakot hasznal, nem kettot: harmas, kettes es egyes zarojel-par. A nev eloszor
# "ket alakkal"-t mondott -- Anorak vette eszre 2026-09-11 01:0x. Egy teszt NEVE allitas a
# tesztrol: aki a listat atfutja, azt latja, es ha valaki kesobb kiveszi a harmadik alakot,
# egy alulmondo nev nem mond ellent neki.
render_check "a tanito mondat mind a HAROM alakkal" \
  'irj {{{id}}}-t ha beszelsz rola, {{id}}-t a magyarazatban, {id}-t mutatohoz' \
  'irj {{id}}-t ha beszelsz rola, {id}-t a magyarazatban, 4520-t mutatohoz'

# --- 3. half a flag pair is an error, not a silent no-op --------------------
expect_arg_error "csak --close"  "needs --result" --close 123 marveen anorak "x"
expect_arg_error "csak --result" "needs --close"  --result "y {id}" marveen anorak "x"

echo
[ "$fails" = 0 ] && echo "MIND ATMENT" || echo "$fails FAIL"
exit $([ "$fails" = 0 ] && echo 0 || echo 1)
