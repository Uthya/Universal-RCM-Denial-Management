import { useTypesStore } from "../store/Types.js";
import { useTypesRender } from "../utils/useRender.js";
export var StringText = props => {
  var _useTypesStore = useTypesStore(),
    _useTypesStore$Str = _useTypesStore.Str,
    Comp = _useTypesStore$Str === void 0 ? {} : _useTypesStore$Str;
  useTypesRender(Comp, props, 'Str');
  return null;
};
StringText.displayName = 'JVR.StringText';