import { useTypesStore } from "../store/Types.js";
import { useTypesRender } from "../utils/useRender.js";
export var Url = props => {
  var _useTypesStore = useTypesStore(),
    _useTypesStore$Url = _useTypesStore.Url,
    Comp = _useTypesStore$Url === void 0 ? {} : _useTypesStore$Url;
  useTypesRender(Comp, props, 'Url');
  return null;
};
Url.displayName = 'JVR.Url';