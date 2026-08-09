
# Dpmaster, an open master server

This maintained fork adds atomic persistence for validated servers, heartbeat
rate limiting, hardened systemd units, a UDP health probe, and Prometheus
metrics and a public status page on HTTP port 80.

## Quick start

```sh
make -C src release
./src/dpmaster --flood-protection --state-file ./servers.state
```

The state file is optional. When configured, validated and unexpired servers
survive a process restart. Writes use a temporary file followed by an atomic
rename, so an interrupted write cannot replace the last good snapshot.

Run the persistence integration test with:

```sh
cd testsuite
./test-persistence.py
```

## Production deployment

Reference systemd units and installation instructions are in
[`contrib/systemd`](contrib/systemd/README.md). That deployment enables flood
protection, persistence, warning-only logs, startup readiness checking, a
one-minute health timer, and HTTP endpoints:

- `GET /healthz` — probes the UDP master and returns HTTP 200 or 503.
- `GET /metrics` — Prometheus text metrics for availability, server counts,
  address families, occupancy, state age, and exporter uptime.
- `GET /` — public, responsive service-status page.
- `GET /api/status.json` — JSON service status and aggregate counts.
- `GET /api/servers.json` — JSON export of the active server list.

The HTTP service uses a bounded worker pool, short socket timeouts and a
per-client token bucket to shed abusive traffic. See the deployment guide for
the configurable HTTP port and network-level protection required against
volumetric DDoS attacks.

To change the public HTTP listener, install
`contrib/systemd/dpmaster-exporter.conf` as
`/etc/default/dpmaster-exporter`, edit `DPMASTER_HTTP_PORT`, then restart
`dpmaster-exporter.service`. The selected TCP port must also be allowed by the
host and provider firewalls.

The Docker image builds the source from this repository and stores persistent
state in the `/var/lib/dpmaster` volume.

## General information


1 INTRODUCTION

2 COMMAND LINE SYNTAX

3 BASIC USAGE

4 CONTACTS & LINKS


### 1 INTRODUCTION:

Dpmaster is a lightweight master server written from scratch for DarkPlaces,
LordHavoc's game engine. It is an open master server because of its free source
code and documentation, and because its Quake III Arena-like protocol allows it
to fully support new games without having to restart or reconfigure it. In
addition to its own protocol, dpmaster also supports the master protocols of
"Quake III Arena" (Q3A), "Return to Castle Wolfenstein" (RtCW), and
"Wolfenstein: Enemy Territory" (WoET).

Several game engines currently support the DP master server protocol: DarkPlaces
and all its derived games (such as Nexuiz and Transfusion), QFusion and most of
its derived games (such as Warsow), and FTE QuakeWorld. Also, IOQuake3 uses it
for its IPv6-enabled servers and clients since its version 1.36. Last but not
least, dpmaster's source code has been used by a few projects as a base for
creating their own master servers (this is the case of Tremulous, for instance).

If you want to use the DP master protocol in one of your software, take a look
at the section "USING DPMASTER WITH YOUR GAME" in "doc/techinfo.txt" for further
explanations. It is pretty easy to implement, and if you ask politely, chances
are you will be able to find someone that will let you use his running dpmaster
if you can't have your own.

Although dpmaster is being primarily developed on a Linux PC, it is regularly
compiled and tested on Windows XP and OpenBSD, including on non-PC hardware when
possible. It has also been run successfully on Mac OS X, FreeBSD, NetBSD and
Windows 2000 in the past, but having no regular access to any of those systems,
I cannot guarantee that it is still the case. In particular, building dpmaster
on Windows 2000 may require some minor source code changes due to the addition
of IPv6 support in dpmaster, Windows 2000 having a limited support for this
protocol.

Take a look at the "COMPILING DPMASTER" section in "doc/techinfo.txt" for more
practical information on how to build it.

The source code of dpmaster is available under the GNU General Public License,
version 2. The complete text of this license is in the file "doc/license.txt".


### 2 COMMAND LINE SYNTAX:

The syntax of the command line is the classic: "dpmaster [options]". Running
"dpmaster -h" will print the available options for your version. Be aware that
some options are only available on UNIXes, including all security-related
options - see the "SECURITY" section in "doc/manual.txt".

All options have a long name (a string), and most of them also have a short name
(one character). In the command line, long option names are preceded by 2
hyphens and short names by 1 hyphen. For instance, you can run dpmaster as a
daemon on UNIX systems by calling either "dpmaster -D" or "dpmaster --daemon".

A lot of options have one or more associated parameters, separated from the
option name and from each other by a blank space. Optionally, you are allowed
to simply append the first parameter to an option name if it is in its short
form, or to separate it from the option name using an equal sign if it is in its
long form. For example, these 4 ways of running dpmaster with a maximum number
of servers of 16 are equivalent:

   * dpmaster -n 16
   * dpmaster -n16
   * dpmaster --max-servers 16
   * dpmaster --max-servers=16


### 3 BASIC USAGE:

For most users, simply running dpmaster, without any particular parameter,
should work perfectly. Being an open master server, it does not require any
game-related configuration. The vast majority of dpmaster's options deal with
how you want to run it: which network interfaces to use, how many servers it
will accept, where to put the log file, etc. And all those options have default
values that should suit almost everyone.

That being said, here are a few options you may find handy.

The most commonly used one is probably "-D" (or "--daemon"), a UNIX-specific
option to make the program run in the background, as a daemon process.

You can also use the verbose option "-v" to make dpmaster print extra
information (see "OUTPUT AND VERBOSITY LEVELS" in "doc/manual.txt").

Finally, if you intent to run dpmaster for a long period of time, you may want
to take a look at the log-related options before starting it (see the LOGGING
section in "doc/manual.txt").

More options and their descriptions can be found in "doc/manual.txt", so feel
free to read this file if you have specific needs.


### 4 CONTACTS & LINKS:

You can get the latest versions of DarkPlaces and dpmaster on the DarkPlaces
home page <http://icculus.org/twilight/darkplaces/>.

If dpmaster doesn't fit your needs, please drop me an email (my name and email
address are right below those lines): your opinion and ideas may be very
valuable to me for evolving it to a better tool.


--
Mathieu Olivier
molivier, at users.sourceforge.net
