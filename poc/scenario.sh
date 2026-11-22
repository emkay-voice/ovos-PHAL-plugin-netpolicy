#!/bin/bash
A() { su - ovos -c "~/.venvs/ovos/bin/python ask2.py $*" 2>&1 | grep -v -e WARN -e '^$'; }
echo "### 1. go offline (Guardian -> netpolicy)"; A '"go offline"' '""' 12
sleep 3
echo "### 1b. while local only: ovos-core vs. the ovos user's own shell"
/root/probe.sh ovos-core.service
echo "$(su - ovos -c 'curl -s -m 6 -o /dev/null -w %{http_code} https://www.wikipedia.org') ovos user's shell (pip, ssh)"
echo "### 2. weather, answer yes"; A '"what is the weather in Copenhagen"' yes 40
echo "### 3. weather, answer no"; A '"what is the weather in Copenhagen"' no 25
echo "### 4. answered from the device (no offer expected)"; A '"who was Lise Meitner"' yes 20
echo "### 5. go online"; A '"go online"' '""' 8
echo "### bridge log"; grep -v WARN /root/np-bus.log | tail -8
