"""
Libraries shipped inside the integration rather than installed at start-up.

`aioproxmox` is mZ738/aioproxmox at commit 39a689bd0a56fcc56c90570dbfb164ae0b95d7df
(MIT, its LICENSE alongside), unchanged. It used to be a manifest requirement
pinned to that GitHub commit, which made Home Assistant pip-install it from
GitHub at every start: no git, no route to GitHub, or GitHub having a bad
minute, and the integration did not load. Its own dependencies, aiohttp and
mashumaro, ship with Home Assistant. Refresh by copying the package over at a
new commit and updating the line above.
"""
