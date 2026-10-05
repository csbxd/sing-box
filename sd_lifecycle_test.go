package box_test

import (
	"context"
	"testing"

	box "github.com/sagernet/sing-box"
	"github.com/sagernet/sing-box/adapter"
	"github.com/sagernet/sing-box/include"
	"github.com/sagernet/sing-box/log"
	"github.com/sagernet/sing-box/option"
	"github.com/sagernet/sing/service"

	"github.com/stretchr/testify/require"
)

func requireBoxDiscoveryClosed(t *testing.T, ctx context.Context) {
	t.Helper()
	discovery := service.FromContext[adapter.ServiceDiscovery](ctx)
	require.NotNil(t, discovery)
	scope := adapter.NewScope(t.Context(), log.NewNOPFactory().Logger())
	defer scope.Close()
	require.ErrorContains(t, discovery.Start(adapter.StartStateInitialize, scope), "sd is closed")
}

func TestServiceDiscoveryBoxLifecycle(t *testing.T) {
	for _, phase := range []string{"before start", "pre-start", "started"} {
		t.Run(phase, func(t *testing.T) {
			ctx := service.ContextWithDefaultRegistry(include.Context(t.Context()))
			instance, err := box.New(box.Options{
				Context: ctx,
				Options: option.Options{Log: &option.LogOptions{Disabled: true}},
			})
			require.NoError(t, err)
			t.Cleanup(func() { instance.Close() })
			switch phase {
			case "pre-start":
				require.NoError(t, instance.PreStart())
			case "started":
				require.NoError(t, instance.Start())
			}
			require.NoError(t, instance.Close())
			require.NoError(t, instance.Close())
			requireBoxDiscoveryClosed(t, ctx)
		})
	}
}

func TestServiceDiscoveryBoxEarlyStartupFailure(t *testing.T) {
	ctx := service.ContextWithDefaultRegistry(include.Context(t.Context()))
	instance, err := box.New(box.Options{
		Context: ctx,
		Options: option.Options{Log: &option.LogOptions{
			Output: t.TempDir(),
		}},
	})
	require.NoError(t, err)
	t.Cleanup(func() { instance.Close() })
	// Opening a directory as a log file fails before component startup.
	require.ErrorContains(t, instance.Start(), "start logger")
	requireBoxDiscoveryClosed(t, ctx)
	require.NoError(t, instance.Close())
}

func TestServiceDiscoveryBoxConstructionFailure(t *testing.T) {
	ctx := service.ContextWithDefaultRegistry(include.Context(t.Context()))
	instance, err := box.New(box.Options{
		Context: ctx,
		Options: option.Options{
			Log:       &option.LogOptions{Disabled: true},
			Outbounds: []option.Outbound{{Type: "missing-sd-test-outbound"}},
		},
	})
	require.ErrorContains(t, err, "initialize outbound")
	require.Nil(t, instance)
	requireBoxDiscoveryClosed(t, ctx)
}
