(() => {
  const panel = document.getElementById("help-faps");
  const result = document.getElementById("help-fap-result");
  const locate = document.getElementById("help-locate");
  const status = document.getElementById("help-location-status");
  const select = document.getElementById("help-fap-select");
  let mapsReady;

  function loadMaps() {
    if (!mapsReady) {
      mapsReady = new Promise((resolve, reject) => {
        const script = document.createElement("script");
        const params = new URLSearchParams({ apikey: panel.dataset.mapsKey, lang: "ru_RU" });
        const fail = () => {
          clearTimeout(timeout);
          script.remove();
          reject(new Error("Map unavailable"));
        };
        const timeout = setTimeout(fail, 10000);
        script.src = `https://api-maps.yandex.ru/v3/?${params}`;
        script.onload = () => {
          const api = window.ymaps3;
          if (!api?.ready) return fail();
          api.ready.then(() => {
            clearTimeout(timeout);
            resolve(api);
          }, fail);
        };
        script.onerror = fail;
        document.head.append(script);
      }).catch((error) => {
        mapsReady = undefined;
        throw error;
      });
    }
    return mapsReady;
  }

  function renderMap(maps, map, point, marker, signal) {
    const groundLayer = `${maps.YMapDefaultSchemeLayer.defaultProps.source}:ground`;
    let listener, timeout, fail;
    return new Promise((resolve, reject) => {
      fail = () => reject(new Error("Map tiles unavailable"));
      timeout = setTimeout(fail, 10000);
      signal.addEventListener("abort", fail, { once: true });
      listener = new maps.YMapListener({
        onStateChanged(state) {
          if (state.getLayerState(groundLayer, "tile")?.tilesReady > 0) resolve();
        },
      });
      map
        .addChild(listener)
        .addChild(new maps.YMapDefaultSchemeLayer({}))
        .addChild(new maps.YMapDefaultFeaturesLayer({}))
        .addChild(new maps.YMapMarker({ coordinates: point }, marker));
    }).finally(() => {
      clearTimeout(timeout);
      signal.removeEventListener("abort", fail);
      if (listener && !signal.aborted) map.removeChild(listener);
    });
  }

  function prepareMap() {
    const toggle = result?.querySelector("[data-show-map]");
    if (!toggle) return { dispose() {}, async show() {} };
    const mapElement = result.querySelector("[data-map]");
    const mapStatus = result.querySelector("[data-map-status]");
    let map, renderController;
    const dispose = () => {
      renderController?.abort();
      map?.destroy();
      map = undefined;
    };

    async function show() {
      if (map) {
        mapElement.hidden = false;
        toggle.textContent = "Скрыть карту";
        toggle.setAttribute("aria-expanded", "true");
        mapStatus.textContent = "";
        return;
      }
      toggle.disabled = true;
      mapStatus.textContent = "Загружаем карту…";
      try {
        const maps = await loadMaps();
        if (!mapElement.isConnected) return;
        // JS API v3 принимает сначала долготу, затем широту.
        const point = [Number(mapElement.dataset.longitude), Number(mapElement.dataset.latitude)];
        const marker = document.createElement("span");
        marker.className = "help-map-marker";
        marker.textContent = "ФАП";
        marker.setAttribute("aria-hidden", "true");
        mapElement.setAttribute("aria-busy", "true");
        mapElement.hidden = false;
        map = new maps.YMap(mapElement, {
          location: { center: point, zoom: 15 },
          behaviors: ["drag", "pinchZoom", "dblClick"],
        });
        renderController = new AbortController();
        await renderMap(maps, map, point, marker, renderController.signal);
        if (!mapElement.isConnected) return;
        toggle.textContent = "Скрыть карту";
        toggle.setAttribute("aria-expanded", "true");
        mapStatus.textContent = "";
      } catch {
        if (!mapElement.isConnected) return;
        dispose();
        mapElement.hidden = true;
        toggle.textContent = "Повторить загрузку карты";
        toggle.setAttribute("aria-expanded", "false");
        mapStatus.textContent = "Карта не загрузилась. Адрес и ссылка на маршрут всё ещё доступны.";
      } finally {
        renderController = undefined;
        mapElement.removeAttribute("aria-busy");
        toggle.disabled = false;
      }
    }

    toggle.hidden = false;
    toggle.addEventListener("click", () => {
      if (map && !mapElement.hidden) {
        mapElement.hidden = true;
        toggle.textContent = "Показать карту";
        toggle.setAttribute("aria-expanded", "false");
      } else {
        show();
      }
    });
    return { dispose, show };
  }

  function setRouteFromPatient(coords, fap) {
    const route = result.querySelector("[data-fap-route]");
    const latitude = Number(fap.dataset.latitude);
    const longitude = Number(fap.dataset.longitude);
    if (!route || !Number.isFinite(latitude) || !Number.isFinite(longitude)) return;
    const params = new URLSearchParams({
      mode: "routes",
      rtext: `${coords.latitude},${coords.longitude}~${latitude},${longitude}`,
      rtt: "auto",
    });
    route.href = `https://yandex.ru/maps/?${params}`;
    route.textContent = "Открыть маршрут от моего места";
    const note = result.querySelector("[data-route-note]");
    if (note) note.textContent = "В Яндекс Картах уже будут указаны ваше место и медпункт.";
  }

  let mapView = prepareMap();
  if (!locate) return;
  if (!window.isSecureContext || !navigator.geolocation) {
    locate.disabled = true;
    status.textContent = "Автоматический поиск здесь недоступен. Выберите медпункт из списка.";
    return;
  }

  locate.addEventListener("click", () => {
    locate.disabled = true;
    status.textContent = "Определяем ваше местоположение…";

    navigator.geolocation.getCurrentPosition(async ({ coords }) => {
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), 10000);
      try {
        const response = await fetch(locate.dataset.url, {
          method: "POST",
          body: new URLSearchParams({ latitude: coords.latitude, longitude: coords.longitude }),
          signal: controller.signal,
          redirect: "error",
        });
        if (!response.ok) throw new Error();
        const html = await response.text();
        mapView.dispose();
        result.innerHTML = html;
        const fap = result.querySelector("[data-fap-id]");
        mapView = prepareMap();
        if (fap) {
          select.value = fap.dataset.fapId;
          setRouteFromPatient(coords, fap);
          const hasMap = Boolean(result.querySelector("[data-map]"));
          status.textContent = hasMap
            ? "Нашли ближайший медпункт. Ниже — адрес, карта и маршрут от вашего места."
            : "Нашли ближайший медпункт. Ниже — адрес и маршрут от вашего места.";
          result.scrollIntoView({
            behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth",
            block: "start",
          });
          // После поиска карта открывается сразу, отдельное нажатие не нужно.
          mapView.show();
        } else {
          status.textContent = "";
        }
      } catch {
        status.textContent = "Поиск не удался. Выберите медпункт из списка.";
      } finally {
        clearTimeout(timeout);
        locate.disabled = false;
      }
    }, (error) => {
      status.textContent = error.code === 1
        ? "Нет доступа к местоположению. Разрешите его в настройках браузера или выберите медпункт из списка."
        : "Не удалось определить местоположение. Выберите медпункт из списка.";
      locate.disabled = false;
    }, { timeout: 15000, maximumAge: 300000 });
  });
})();
