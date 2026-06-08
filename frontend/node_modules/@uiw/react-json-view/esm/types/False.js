import { useTypesStore } from "../store/Types.js";
import { useTypesRender } from "../utils/useRender.js";
export var False = props => {
  var _useTypesStore = useTypesStore(),
    _useTypesStore$False = _useTypesStore.False,
    Comp = _useTypesStore$False === void 0 ? {} : _useTypesStore$False;
  useTypesRender(Comp, props, 'False');
  return null;
};
False.displayName = 'JVR.False';