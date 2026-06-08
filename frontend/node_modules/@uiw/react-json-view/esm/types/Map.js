import { useTypesStore } from "../store/Types.js";
import { useTypesRender } from "../utils/useRender.js";
export var Map = props => {
  var _useTypesStore = useTypesStore(),
    _useTypesStore$Map = _useTypesStore.Map,
    Comp = _useTypesStore$Map === void 0 ? {} : _useTypesStore$Map;
  useTypesRender(Comp, props, 'Map');
  return null;
};
Map.displayName = 'JVR.Map';