#!/bin/bash
# probe.sh <service>: curl wikipedia as the ovos user from inside that service's cgroup
CG=/sys/fs/cgroup/user.slice/user-1000.slice/user@1000.service/app.slice/$1
bash -c "echo \$\$ > $CG/cgroup.procs && exec setpriv --reuid 1000 --regid 1000 --clear-groups curl -s -m 6 -o /dev/null -w '%{http_code}' https://www.wikipedia.org"
echo " $1"
