package build_shared

import "testing"

func TestCustomBuildVersion(t *testing.T) {
	for _, version := range []string{"1.14.0-c1", "1.15.0-alpha.9.c2"} {
		t.Setenv("SING_BOX_BUILD_VERSION", version)
		got, err := ReadTag()
		if err != nil || got != version {
			t.Fatalf("ReadTag() = %q, %v; want %q", got, err, version)
		}
	}
}

func TestInvalidCustomBuildVersion(t *testing.T) {
	t.Setenv("SING_BOX_BUILD_VERSION", "1.2.3\ninvalid=true")
	if _, err := ReadTag(); err == nil {
		t.Fatal("invalid override accepted")
	}
}
