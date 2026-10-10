# Contributing

Contributions are welcome. See [BUILDING.md](BUILDING.md) for how to build and test.

By contributing you agree that your contribution is licensed under the same terms as the rest of the project
(GPL-2.0-or-later, see `LICENSE`).

## For maintainers

Releases are cut by pushing a version tag, which triggers the whole build + release pipeline.

```
git tag v1.0.0
git push origin v1.0.0
```

That runs the tests, builds for all platforms currently supported (smoke-testing each built app), and then creates a
**draft** GitHub Release with all the artifacts attached. Review it and publish it by hand.

Every release also carries `akaiutil-4.6.8.tar.gz`, the exact source of the akaiutil copy bundled in the apps. akaiutil
is GPL-2, so shipping its binary comes with an obligation to offer its source; the workflow downloads it from
SourceForge and checks it against the SHA-256 pinned in `src/akaiutil_gui/engine/fetch.py`. If you ever change the
bundled akaiutil version, update that pin and the checksum in `src/akaiutil_gui/ui/help/THIRD_PARTY_NOTICES.md`
(a test fails if they disagree).

The workflow can also be started by hand from the Actions tab (`workflow_dispatch`): it runs the tests and builds but,
without a tag, doesn't make a release.
