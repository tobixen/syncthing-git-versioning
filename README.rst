This project is a fork of `syncthing-git-versioning`_.  The upstream vision is to use git as a storage
for old file revisions, presumably for backup purposes (though the
README explains why the hook is not a replacement for a good
backup tool).  I want to use it for syncing the contents of a git-controlled work directory to remote hosts.  While those visions could be combined in the same tool, the upstream maintainer rejected it - see
https://github.com/basak/syncthing-git-versioning/issues/3 and https://github.com/basak/syncthing-git-versioning/pull/4

syncthing-git
=============

.. A single sentence that says what the product is, succinctly and memorably

syncthing-git-versioning is an external versioning hook for `Syncthing`_ allowing contents in a git-controlled repository to be synced to other nodes.

.. A paragraph of one to three short sentences, that describe what the product
   does.

This hook will ensure uncommitted local changes aren't irreversibly overwritten by Syncthing.  It will not handle syncing of the git directories, but safeguards are provided to prevent Syncthing from corrupting the git repostitory.

.. A third paragraph of similar length, this time explaining what need the
   product meets.

Git is the de-facto standard for versioning today.  While Syncthing offers "versioning", it lacks native support for syncing git
repositories in a safe way.

.. Finally, a paragraph that describes whom the product is useful for.

This script is designed to work on Linux-systems.  This tool is useful
for Syncthing users with basic git knowledge.

Caveats
-------

* Currently the hook is designed to be used on one "main" node, all
  git-operations are supposed to be done from there (at least
  commits), and only read-operations and quick edits are supposed to
  be run from the "satellites".  Support for bi-directional syncing of
  the git-directory may be supported in an upcoming version (but
  probably it would be better to implement native git-support in
  Syncthing).

* The hook does not handle conflict resolution in any way.

* You are not supposed to be doing things like complex interactive git
  rebasing, checking out old revisions, etc.  Any temporary local
  changes will be mirrored out on the "satellites".  (consider using
  the git worktree feature).

* If running this hook on several nodes, you may get different and
  incompatible git histories.

* This hook is in itself not a replacement for a good backup system -
  it will not keep snapshot information or information on when files
  have been added - but if you combine this hook with a timer/cron
  script doing ``git add . ; git commit -am "hourly autocommit" ; git push``
  then the need for a proper backup system is probably moot.


Quick Start
-----------

1. Install by running ``make install`` from the source directory. This installs
   to ``/usr/local/bin/`` when run as root, or to ``~/.local/bin/`` otherwise.

2. Run ``syncthing-git-versioning setup`` to configure an existing Syncthing folder
   interactively. It will connect to your local Syncthing instance via its REST
   API, let you pick a folder, initialise a git repository inside that folder,
   and apply the configuration without requiring a restart.

3. Test by changing and deleting files in the sync folder on other devices.
   After Syncthing has synced the changes, inspect the git repository. You
   should see old versions of changed and deleted files appearing there.

4. The indended usage pattern is that you manually curate the git
   repo.  If committing frequently, this hook will most of the time do
   nothing.  If you only want to use git for versioning, then I'd
   suggest adding a timer/cron script for regularly committing
   snapshots and adding files.


Details
-------

* The only dependencies are git itself, ``/bin/sh`` and common shell tools. You
  can generally expect all of these to be available as part of any modern Linux
  base system.

* The tool supports `git-annex`_, just use ``git annex init`` as normal. This tool will
  automatically detect that an annex is present, and use ``git annex add``
  instead of plain ``git add``. For finer control of what gets added to the
  annex, you can configure ``annex.largefiles`` directly. See
  `git-annex-config(1)`_ for details.

* If a file changes to a directory or vice versa, then inserting them into a git
  repository can get complicated. This tool is intended to handle all of this
  for you.

* If you use AppArmor to confine syncthing (this isn't the default), then you
  will need to add rules to allow this tool to do its work. See the
  ``apparmor/`` directory for an example.

* A test suite is included, it depends on git-annex and pytest. Run it
  with ``make test``, or directly with ``pytest``.

License
-------

This tool and associated files are subject to the terms of the Mozilla Public
License, v. 2.0. A copy of the license is included in the file ``LICENSE`` in the
source repository, or you can obtain it from https://mozilla.org/MPL/2.0/.

As an exception, files found anywhere under ``apparmor.d/`` are distributed
under the terms of the GNU General Public License, version 3. See the file
``COPYING`` in that directory for the licence text.

.. _Syncthing: https://syncthing.net/
.. _Syncthing-git-versioning: https://github.com/basak/syncthing-git-versioning/
.. _git-annex: https://git-annex.branchable.com/
.. _git-annex-config(1): https://git-annex.branchable.com/git-annex-config/
